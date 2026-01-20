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
    pid = os.getpid()
    
    def _monitor():
        # Allow brief startup grace period
        time.sleep(2.0)
        
        while not stop_event.is_set():
            try:
                curr_ppid = os.getppid()
                parent_dead = False
                
                # On Linux, if parent dies, ppid becomes 1 (init) or a subreaper
                # Only treat ppid=1 as death if we didn't start with ppid=1
                if curr_ppid != orig_ppid:
                    parent_dead = True
                elif curr_ppid == 1 and orig_ppid != 1:
                    parent_dead = True
                else:
                    try:
                         # Signal 0 checks if process exists
                         if orig_ppid != 1: # Don't try to kill(1, 0) usually
                             os.kill(orig_ppid, 0)
                    except OSError:
                         parent_dead = True
                
                if parent_dead:
                    msg = f"[{label}] Parent {orig_ppid} gone (curr: {curr_ppid}, self: {pid}). Initiating shutdown."
                    try:
                        logger.warning(msg)
                        print(msg)
                    except:
                        pass
                        
                    stop_event.set()
                    
                    # Give the main loop a moment to see stop_event and exit cleanly
                    time.sleep(2.0)
                    
                    # Force exit if still alive
                    try:
                        logger.warning(f"[{label}] Force terminating self PID {pid} (parent gone).")
                    except:
                        pass
                    os.kill(pid, signal.SIGKILL)
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