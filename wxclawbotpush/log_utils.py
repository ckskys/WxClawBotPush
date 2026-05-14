"""日志工具模块：配置日志记录器，同时输出到控制台和内存缓冲区。"""
import logging
import threading
from collections import deque
from datetime import datetime

log_buffer: "deque[dict]" = deque(maxlen=1000)  # 内存日志缓冲区，供管理面板查看
log_buffer_lock: threading.Lock = threading.Lock()


class LogBufferHandler(logging.Handler):
    """日志处理器：将结构化日志记录追加到内存缓冲区。"""

    def emit(self, record: logging.LogRecord):
        entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "message": self.format(record),
        }
        with log_buffer_lock:
            log_buffer.append(entry)


def setup_logging() -> logging.Logger:
    """创建 logger 实例，同时添加控制台输出和缓冲区处理器。"""
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
