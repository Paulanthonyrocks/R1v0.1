import multiprocessing
import sys

def check_qsize():
    q = multiprocessing.Queue(maxsize=10)
    try:
        sz = q.qsize()
        print(f"qsize supported, size={sz}")
    except NotImplementedError:
        print("qsize NOT supported")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_qsize()
