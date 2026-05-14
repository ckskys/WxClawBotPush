import logging
import threading
from collections import deque
from datetime import datetime

log_buffer: deque = deque(maxlen=1000)
log_buffer_lock = threading.Lock()


class LogBufferHandler(logging.Handler):
    def emit(self, record):
        entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "message": self.format(record),
        }
        with log_buffer_lock:
            log_buffer.append(entry)


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("wxclawbotpush")
    logger.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(console_handler)

    buffer_handler = LogBufferHandler()
    buffer_handler.setLevel(logging.INFO)
    buffer_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(buffer_handler)

    return logger
