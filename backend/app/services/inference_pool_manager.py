from typing import Dict, Any, List, Optional
import logging
import os
import time
import multiprocessing
from multiprocessing import Process
import torch
from app.core.inference_worker import inference_worker
from app.utils.distributed_queue import RedisQueue
from app.services.constants import FeedManagerConstants

logger = logging.getLogger("app.services.inference_pool_manager")


def _resolve_gpu_pin(worker_id: int, num_gpus: int, gpu_mode: bool) -> Optional[Dict[str, str]]:
    """Env overrides that pin a worker to one physical GPU at spawn time.

    CUDA_VISIBLE_DEVICES is read by torch at the child's FIRST CUDA call
    (fresh interpreter under spawn), so setting it in the parent's
    os.environ before Process.start() deterministically gives the worker a
    single-GPU view -- the exact condition under which every standalone
    engine probe on this hardware passes (a 2xT4 box whose cuda:1 faults
    deterministically at the first TRT kernel only when the pool's workers
    see BOTH GPUs). R1_PHYSICAL_GPU_ID lets the worker log and flock on the
    PHYSICAL device, since its logical view is always cuda:0 when pinned.
    Returns None when GPU mode is off so callers can clear stale pins.
    """
    if not gpu_mode or num_gpus <= 0:
        return None
    gpu_id = worker_id % num_gpus
    return {"R1_PHYSICAL_GPU_ID": str(gpu_id), "CUDA_VISIBLE_DEVICES": str(gpu_id)}

class InferencePoolManager:
    """
    Manages the lifecycle and scaling of inference workers.
    Handles slot assignment, worker spawning, and dynamic pool resizing.
    """
    def __init__(self, config: Dict[str, Any], slot_count: int, 
                 inference_input_queues: List[Any], db_queue: Any, 
                 stop_event: Any):
        self.config = config
        self.slot_count = slot_count
        self._inference_input_queues = inference_input_queues
        self._db_queue = db_queue
        self._inference_stop_event = stop_event
        
        self._is_shutting_down = False
        self._inference_pool: Dict[int, Process] = {}
        self._inference_command_queues: Dict[int, RedisQueue] = {}
        self._slot_to_worker: Dict[int, int] = {}

        # Per-worker GPU pinning. Every reproduction probe that PASSED on
        # this hardware ran with exactly ONE GPU visible to the process
        # (CUDA_VISIBLE_DEVICES=1); the only configuration that faults
        # (cudaErrorIllegalAddress on the first TRT kernel, deterministically,
        # in ~50 fresh processes across 3 runs) is the pool where every
        # worker sees both T4s. Pin each worker to its physical GPU at spawn
        # so all workers run under the proven-good single-device condition.
        # Workers are spawned sequentially, so per-spawn os.environ mutation
        # is race-free: each child snapshots the env at its own start().
        self._gpu_mode = False
        self._num_gpus = 0
        try:
            use_gpu = bool((config.get("performance") or {}).get("gpu_acceleration", False))
            if use_gpu and torch.cuda.is_available():
                self._gpu_mode = True
                self._num_gpus = torch.cuda.device_count()
        except Exception:
            self._gpu_mode = False
            self._num_gpus = 0
        # Worker ids permanently removed from the pool after repeated
        # CUDA-fatal exits (see feed_watchdog watchdog_loop). Quarantined
        # workers are never respawned and never receive feed routing; their
        # slots are orphaned so scale_pool can rebalance them to survivors.
        self._quarantined_workers: set = set()

    @property
    def quarantined_workers(self) -> set:
        return set(self._quarantined_workers)

    def quarantine_worker(self, worker_id: int, reason: str) -> List[int]:
        """Permanently removes a worker from the pool after repeated fatal
        failures (CUDA-fatal exits, exit code 42). A device whose first
        compute kernel faults in a fresh process is a hardware failure --
        respawning just spreads failures out. Returns the slot ids that were
        owned so the caller can halt the feeds routed there before the dead
        slot queue exhausts the SHM free pool.

        The slots are ORPHANED (removed from _slot_to_worker) so the next
        scale_pool pass reassigns them to surviving workers; with a pinned
        pool the reassignment also respawns the slot's new owner (slot sets
        are read once at spawn), which is a one-time model reload per
        quarantine.
        """
        slots = [s for s, w in self._slot_to_worker.items() if w == worker_id]
        self._quarantined_workers.add(worker_id)
        self._inference_pool.pop(worker_id, None)
        self._inference_command_queues.pop(worker_id, None)
        for s in slots:
            del self._slot_to_worker[s]
        logger.warning(
            f"QUARANTINED InferenceWorker-{worker_id}: {reason}. "
            f"Pool is now {len(self._inference_pool)} workers; released slots {slots}."
        )
        return slots

    def spawn_worker(self, worker_id: int):
        """Spawns a single inference worker assigned to specific slots."""
        if worker_id in self._quarantined_workers:
            logger.warning(
                f"Refusing to spawn InferenceWorker-{worker_id}: quarantined "
                f"(repeated CUDA-fatal exits). Restart it manually after the "
                f"GPU/device issue is resolved."
            )
            return
        if self._is_shutting_down:
            logger.warning(f"Shutdown in progress. Refusing to spawn worker {worker_id}.")
            return

        # Clear any stale stop signal from a terminated predecessor to prevent immediate exit
        if self._inference_stop_event and hasattr(self._inference_stop_event, "clear"):
            try:
                self._inference_stop_event.clear()
            except Exception as e:
                logger.warning(f"Could not clear inference stop event before spawning worker {worker_id}: {e}")

        slots = [s for s, w in self._slot_to_worker.items() if w == worker_id]

        # Refuse to spawn a worker with no slot ownership. This happens when
        # scale_pool's modulo-based assignment overwrites a survivor's slot
        # claim before that survivor respawns: the new worker reads the map,
        # finds no slots for itself, and would otherwise sit idle forever
        # burning a process + a CUDA context. (Log evidence: Workers 3 and 7
        # in 14:20:05 backend_main.log both reported Slots assigned: [3],
        # but slot 3 is never reachable by the feed-routing formula in
        # feed_manager.start_feed, so neither produced any METRICS line.)
        if not slots:
            logger.warning(
                f"Refusing to spawn InferenceWorker-{worker_id}: no slots "
                f"assigned. _slot_to_worker={self._slot_to_worker}."
            )
            return

        cmd_q = RedisQueue(f'worker_cmd_{worker_id}', maxsize=100)

        # Clear any leftover commands from a previous worker with the same ID
        try:
            while True:
                cmd_q.get_nowait()
        except Exception:
            pass

        self._inference_command_queues[worker_id] = cmd_q

        p = Process(
            target=inference_worker,
            args=(
                worker_id,
                self._inference_input_queues,
                cmd_q,
                self._inference_stop_event,
                dict(self.config) if hasattr(self.config, 'model_dump') else self.config,
                self._db_queue,
                None,   # frame_buffer — handled inside worker
                None,   # pipeline_pressure — worker reads from Redis
                slots,
            ),
            daemon=False,
            name=f"InferenceWorker-{worker_id}",
        )
        # Pin the worker to ONE physical GPU BEFORE the child starts: the
        # child inherits os.environ and torch fixes its device list at the
        # child's first CUDA call (fresh interpreter under spawn), so the
        # pin is guaranteed even though this parent may have CUDA inited
        # with both GPUs visible. See _resolve_gpu_pin.
        pin = _resolve_gpu_pin(worker_id, self._num_gpus, self._gpu_mode)
        if pin is not None:
            os.environ.update(pin)
            logger.info(
                f"Pinning InferenceWorker-{worker_id} to physical GPU "
                f"{pin['CUDA_VISIBLE_DEVICES']} "
                f"(CUDA_VISIBLE_DEVICES={pin['CUDA_VISIBLE_DEVICES']})"
            )
        else:
            # GPU mode off: don't leak a stale pin from an earlier GPU-mode spawn.
            os.environ.pop("R1_PHYSICAL_GPU_ID", None)
        p.start()
        self._inference_pool[worker_id] = p
        logger.info(f"Launched InferenceWorker-{worker_id} handling slots {slots}")

        # Brief liveness check after spawn
        time.sleep(0.5)
        if p.is_alive():
            logger.info(f"InferenceWorker-{worker_id} (PID {p.pid}) is alive after spawn")
        else:
            logger.error(
                f"InferenceWorker-{worker_id} (PID {p.pid}) DIED immediately "
                f"with exitcode={p.exitcode}"
            )

    def scale_pool(self, target_size: int):
        """Dynamically adjusts the number of active workers and rebalances slots.

        Slot ownership lives in ``self._slot_to_worker``. Workers read their
        slots ONCE at spawn time (see spawn_worker), so ANY reassignment made
        here MUST be followed by a respawn of the affected workers, or the
        change is invisible to them. The previous code reassigned orphaned
        slots to survivors on scale-down but never told the survivors -- so
        those slots were drained by NO worker (Crack A: silent inference loss
        on a live feed). This rewrite respawns only the survivors whose slot
        set actually changed, reusing spawn_worker's map re-read.
        """
        target_size = max(FeedManagerConstants.MIN_WORKERS, min(target_size, FeedManagerConstants.MAX_WORKERS))
        current_size = len(self._inference_pool)

        if target_size == current_size:
            return

        logger.info(f"Scaling inference pool: {current_size} -> {target_size}")

        # Snapshot each worker's slot set BEFORE mutation, so we can later
        # detect which survivors must be respawned to pick up new slots.
        old_slot_sets = {
            wid: {s for s, w in self._slot_to_worker.items() if w == wid}
            for wid in self._inference_pool
        }

        # 1. Terminate excess workers (wid >= target_size).
        workers_to_kill = [wid for wid in self._inference_pool if wid >= target_size]
        for wid in workers_to_kill:
            p = self._inference_pool.pop(wid)
            try:
                p.terminate()
            except Exception as e:
                logger.warning(f"Error terminating InferenceWorker-{wid}: {e}")
            self._inference_command_queues.pop(wid, None)
            logger.debug(f"Terminated InferenceWorker-{wid} during scale-down")

        # Drop the killed workers' slot ownership so those slots become orphaned.
        for slot in [s for s, w in self._slot_to_worker.items() if w in set(workers_to_kill)]:
            del self._slot_to_worker[slot]

        effective_workers = min(target_size, self.slot_count)
        survivors = list(self._inference_pool.keys())

        # 2. Reassign orphaned slots round-robin across survivors, using the
        #    SAME fan-out formula as spawn_worker
        #    (slot % effective_workers == wid % effective_workers) so the
        #    mapping stays consistent with what spawn_worker assigns to any
        #    newly-spawned worker.
        for slot in range(self.slot_count):
            if slot in self._slot_to_worker:
                continue  # still owned by a survivor
            if not survivors:
                continue
            owner = (slot % effective_workers) % len(survivors)
            self._slot_to_worker[slot] = survivors[owner]

        # 3. Spawn new workers on scale-up. spawn_worker reads slots fresh
        #    from _slot_to_worker, so new workers get correct ownership.
        #
        #    CRITICAL: when target_size > slot_count (oversubscribed pool),
        #    we CANNOT give every new wid a distinct slot. Previously the
        #    loop assigned slot s to wid N if (s % effective_workers ==
        #    N % effective_workers), which OVERWROTE a survivor's slot
        #    claim mid-iteration. Survivors whose slot got stolen ended
        #    up with empty sets; step 4 detected the change and tried
        #    to respawn them, but patch 1 (orphan gate) refused because
        #    their new set was empty -- leaving the worker dead.
        #
        #    Fix: prefer an UNOWNED slot for the new worker. Only fall
        #    back to the modulo formula if every slot is already taken.
        #    This preserves the invariant "every alive worker owns at
        #    least one slot" regardless of pool_size vs slot_count.
        for wid in range(target_size):
            if wid not in self._inference_pool:
                claimed = False
                # First pass: any unowned slot works
                for slot in range(self.slot_count):
                    if slot not in self._slot_to_worker:
                        self._slot_to_worker[slot] = wid
                        claimed = True
                        break
                # Second pass: fall back to the modulo formula (oversubscribed)
                if not claimed:
                    for slot in range(self.slot_count):
                        if self._slot_to_worker.get(slot, -1) == wid:
                            claimed = True
                            break
                        if (slot % effective_workers) == (wid % effective_workers):
                            self._slot_to_worker[slot] = wid
                            claimed = True
                            break
                self.spawn_worker(wid)

        # 4. Respawn surviving workers whose slot set CHANGED so they actually
        #    drain their newly-inherited slots (Crack A fix). spawn_worker
        #    re-reads _slot_to_worker, so a respawn is the reliable way to apply
        #    the reassignment. We only restart workers that existed before this
        #    call AND whose set changed -- freshly-spawned workers (step 3) are
        #    already correct and must NOT be double-spawned.
        #
        #    ALSO: respawn any survivor whose new slot set is EMPTY (orphaned).
        #    This handles the case where a worker's slot was reassigned to a
        #    newcomer and the survivor was left with nothing to drain. Previously
        #    such a worker would sit alive in the pool consuming a CUDA context
        #    but producing zero frames. With the new step-3 logic (prefer unowned
        #    slots) this is rare, but the respawn gate is a defense-in-depth.
        new_slot_sets = {
            wid: {s for s, w in self._slot_to_worker.items() if w == wid}
            for wid in self._inference_pool
        }
        for wid in list(self._inference_pool):
            if wid not in old_slot_sets:
                continue  # spawned fresh above; correct as-is
            new_set = new_slot_sets.get(wid, set())
            if old_slot_sets[wid] != new_set or not new_set:
                if not new_set:
                    logger.warning(
                        f"Scale: worker {wid} has no slots (orphaned) -> respawning."
                    )
                else:
                    logger.info(
                        f"Scale: worker {wid} slot set changed "
                        f"{sorted(old_slot_sets[wid])} -> {sorted(new_set)}; respawning."
                    )
                p = self._inference_pool.pop(wid)
                try:
                    p.terminate()
                except Exception as e:
                    logger.warning(f"Error terminating InferenceWorker-{wid} for respawn: {e}")
                self._inference_command_queues.pop(wid, None)
                self.spawn_worker(wid)


    def stop_pool(self):
        """Terminates all workers in the pool."""
        for wid in sorted(self._inference_pool.keys(), reverse=True):
            p = self._inference_pool.pop(wid)
            p.terminate()
            self._inference_command_queues.pop(wid, None)
        logger.info("Inference worker pool stopped.")

    def cleanup(self):
        """Synchronous cleanup for atexit."""
        for p in list(self._inference_pool.values()):
            if p.is_alive():
                p.terminate()
                p.join(timeout=0.5)
                if p.is_alive():
                    p.kill()
        self._inference_pool.clear()
        self._inference_command_queues.clear()
        logger.info("Inference pool cleaned up.")

    @property
    def pool_size(self) -> int:
        return len(self._inference_pool)

    @property
    def slot_to_worker(self) -> Dict[int, int]:
        return self._slot_to_worker

    def get_dead_workers(self) -> List[int]:
        """Returns a list of worker IDs whose processes have died."""
        dead = [wid for wid, p in self._inference_pool.items() if not p.is_alive()]
        if dead:
            logger.warning(f"Detected {len(dead)} dead inference workers: {dead}")
        return dead

    async def respawn_dead_workers(self, dead_workers: List[int]):
        """Respawns a list of dead inference workers."""
        for wid in dead_workers:
            logger.info(f"Respawning dead InferenceWorker-{wid}...")
            # We don't need to re-calculate slots as they are stored in _slot_to_worker
            self.spawn_worker(wid)

