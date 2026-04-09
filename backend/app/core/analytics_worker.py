import queue

try:
    self.output_queue.put((feed_id, feed_metrics, vehicles, lanes, lines), block=False)
except queue.Full:
    # Fix: Enforce hard boundary to prevent OOM. Drop UI update if queue is full.
    pass