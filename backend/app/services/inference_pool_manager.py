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
        
        self._inference_pool: Dict[int, Process] = {}
        self._inference_command_queues: Dict[int, RedisQueue] = {}
        self._slot_to_worker: Dict[int, int] = {}

    def spawn_worker(self, worker_id: int):
        """Spawns a single inference worker assigned to specific slots."""
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
        """Dynamically adjusts the number of active workers and rebalances slots."""
        target_size = max(FeedManagerConstants.MIN_WORKERS, min(target_size, FeedManagerConstants.MAX_WORKERS))
        current_size = len(self._inference_pool)

        if target_size == current_size:
            return

        logger.info(f"Scaling inference pool: {current_size} -> {target_size}")

        # 1. Rebalance slots
        old_slot_to_worker = self._slot_to_worker.copy()
        self._slot_to_worker = {slot: (slot % target_size) for slot in range(self.slot_count)}

        # 2. Terminate excess workers
        if target_size < current_size:
            for wid in sorted(self._inference_pool.keys(), reverse=True):
                if wid >= target_size:
                    p = self._inference_pool.pop(wid)
                    p.terminate()
                    self._inference_command_queues.pop(wid, None)

        # 3. Restart workers whose slot assignments have changed
        for wid in range(target_size):
            needs_restart = False
            if wid not in self._inference_pool:
                needs_restart = True
            else:
                assigned_slots = [s for s, w in self._slot_to_worker.items() if w == wid]
                old_assigned_slots = [s for s, w in old_slot_to_worker.items() if w == wid]
                if assigned_slots != old_assigned_slots:
                    needs_restart = True

            if needs_restart:
                if wid in self._inference_pool:
                    self._inference_pool[wid].terminate()
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

