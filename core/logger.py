"""
日志系统
- 操作日志: logs/operations.log
- 错误日志: logs/errors.log
- 启动日志: 内存缓冲 + 文件备份
"""
import logging
import os
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from datetime import datetime


class AppLogger:
    """应用日志管理器"""

    def __init__(self, app_dir=None):
        if app_dir is None:
            app_dir = Path(__file__).parent.parent
        self.app_dir = Path(app_dir)
        self.logs_dir = self.app_dir / "logs"
        self.logs_dir.mkdir(exist_ok=True)

        self._operation_logger = None
        self._error_logger = None
        self._startup_buffer = []  # 启动日志内存缓冲
        self._max_buffer = 5000

        self._setup_loggers()

    def _setup_loggers(self):
        """配置日志记录器"""
        # 操作日志
        op_handler = RotatingFileHandler(
            self.logs_dir / "operations.log",
            maxBytes=5*1024*1024,  # 5MB
            backupCount=10,
            encoding='utf-8'
        )
        op_handler.setLevel(logging.INFO)
        op_formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        op_handler.setFormatter(op_formatter)

        self._operation_logger = logging.getLogger('llamalauncher.op')
        self._operation_logger.setLevel(logging.INFO)
        self._operation_logger.addHandler(op_handler)
        self._operation_logger.propagate = False

        # 错误日志
        err_handler = RotatingFileHandler(
            self.logs_dir / "errors.log",
            maxBytes=5*1024*1024,
            backupCount=10,
            encoding='utf-8'
        )
        err_handler.setLevel(logging.ERROR)
        err_formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s\n%(exc_info)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        err_handler.setFormatter(err_formatter)

        self._error_logger = logging.getLogger('llamalauncher.err')
        self._error_logger.setLevel(logging.ERROR)
        self._error_logger.addHandler(err_handler)
        self._error_logger.propagate = False

    def info(self, message):
        """记录信息日志"""
        ts = datetime.now().strftime('%H:%M:%S')
        line = f"[{ts}] {message}"
        self._startup_buffer.append(line)
        if len(self._startup_buffer) > self._max_buffer:
            self._startup_buffer = self._startup_buffer[-self._max_buffer:]
        if self._operation_logger:
            self._operation_logger.info(message)

    def error(self, message, exc_info=False):
        """记录错误日志"""
        ts = datetime.now().strftime('%H:%M:%S')
        line = f"[{ts}] ERROR: {message}"
        self._startup_buffer.append(line)
        if self._error_logger:
            self._error_logger.error(message, exc_info=exc_info)

    def warning(self, message):
        """记录警告日志"""
        ts = datetime.now().strftime('%H:%M:%S')
        line = f"[{ts}] WARN: {message}"
        self._startup_buffer.append(line)
        if self._operation_logger:
            self._operation_logger.warning(message)

    def debug(self, message):
        """记录调试日志"""
        if self._operation_logger:
            self._operation_logger.debug(message)

    def get_startup_buffer(self):
        """获取启动日志缓冲"""
        return list(self._startup_buffer)

    def clear_buffer(self):
        """清空日志缓冲"""
        self._startup_buffer.clear()

    def save_startup_log(self, filename=None):
        """保存启动日志到文件"""
        if filename is None:
            filename = self.logs_dir / f"startup_{datetime.now():%Y%m%d_%H%M%S}.log"
        filepath = Path(filename)
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write('\n'.join(self._startup_buffer))
            return filepath
        except Exception as e:
            self.error(f"\u4fdd\u5b58\u542f\u52a8\u65e5\u5fd7\u5931\u8d25: {e}")
            return None


# 全局日志实例
_logger_instance = None

def get_logger(app_dir=None):
    """获取全局日志实例"""
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = AppLogger(app_dir)
    return _logger_instance
