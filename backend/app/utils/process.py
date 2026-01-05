import os
import time
import signal
import threading
import logging
from typing import Any

logger = logging.getLogger("utils.process")

def start_parent_monitor(stop_event: Any, label: str = "Global"):
    """
    Starts a background thread that monitors the parent process.
    If the parent process dies or changes (e.g., during a reload),
    it triggers the stop_event and forces termination if necessary.
    """
    orig_ppid = os.getppid()
    
    def _monitor():
        # Allow brief startup grace period
        time.sleep(2.0)
        
        while not stop_event.is_set():
            try:
                curr_ppid = os.getppid()
                parent_dead = False
                
                # On Linux, if parent dies, ppid becomes 1 (init) or a subreaper
                if curr_ppid != orig_ppid:
                    parent_dead = True
                else:
                    try:
                         # Signal 0 checks if process exists
                         os.kill(orig_ppid, 0)
                    except OSError:
                         parent_dead = True
                
                if parent_dead:
                    logger.warning(f"[{label}] Parent {orig_ppid} gone (curr: {curr_ppid}). Initiating shutdown.")
                    stop_event.set()
                    
                    # Give the main loop a moment to see stop_event and exit cleanly
                    time.sleep(3.0)
                    
                    # Force exit if still alive
                    logger.warning(f"[{label}] Force terminating self (parent gone).")
                    os.kill(os.getpid(), signal.SIGKILL)
                    break
                    
            except Exception as e:
                try:
                    logger.error(f"[{label}] Parent monitor error: {e}")
                except:
                    pass
                
            time.sleep(2.0)

    monitor_thread = threading.Thread(target=_monitor, daemon=True, name=f"ParentMonitor-{label}")
    monitor_thread.start()
    return monitor_thread