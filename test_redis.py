import redis
import logging
import sys

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("test")

def test():
    try:
        logger.info("Testing decode_responses=False")
        r_raw = redis.Redis(host='localhost', port=6379, db=0, decode_responses=False)
        logger.info(f"Ping raw: {r_raw.ping()}")
        
        logger.info("Testing decode_responses=True")
        r_dec = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
        logger.info(f"Ping decoded: {r_dec.ping()}")
        
        logger.info("All tests passed!")
    except Exception as e:
        logger.error(f"Test failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test()
