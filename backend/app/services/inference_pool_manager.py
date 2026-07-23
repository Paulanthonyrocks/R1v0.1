from typing import Dict, Any, List, Optional
import logging
import time
import multiprocessing
from multiprocessing import Process
from app.core.inference_worker import inference_worker
from app.utils.distributed_queue import RedisQueue
from app.services.constants import FeedManagerConstants

logger = logging.getLogger("app.services.inference_pool_manager")

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

    def spawn_worker(self, worker_id: int):
        """Spawns a single inference worker assigned to specific slots."""
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
        for wid in range(target_size):
            if wid not in self._inference_pool:
                for slot in range(self.slot_count):
                    if self._slot_to_worker.get(slot, -1) == wid:
                        continue
                    if (slot % effective_workers) == (wid % effective_workers):
                        self._slot_to_worker[slot] = wid
                self.spawn_worker(wid)

        # 4. Respawn surviving workers whose slot set CHANGED so they actually
        #    drain their newly-inherited slots (Crack A fix). spawn_worker
        #    re-reads _slot_to_worker, so a respawn is the reliable way to apply
        #    the reassignment. We only restart workers that existed before this
        #    call AND whose set changed -- freshly-spawned workers (step 3) are
        #    already correct and must NOT be double-spawned.
        new_slot_sets = {
            wid: {s for s, w in self._slot_to_worker.items() if w == wid}
            for wid in self._inference_pool
        }
        for wid in list(self._inference_pool):
            if wid not in old_slot_sets:
                continue  # spawned fresh above; correct as-is
            if old_slot_sets[wid] != new_slot_sets.get(wid):
                logger.info(
                    f"Scale: worker {wid} slot set changed "
                    f"{sorted(old_slot_sets[wid])} -> {sorted(new_slot_sets.get(wid, set()))}; respawning."
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

