"""统一的 UI 调度器
所有后台线程通过 submit(func, *args, **kwargs) 把 UI 刷新提交到主线程执行，
避免多个独立 after 定时器、避免后台线程直接操作 tkinter 控件。
"""
import queue
import threading
import tkinter as tk
import sys
import os
import time
import traceback


def _log_executor_error():
    """把 UI 调度器里执行失败的异常写到 logs/errors.log，便于诊断"""
    try:
        app_dir = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv else os.getcwd()
        log_dir = os.path.join(app_dir, 'logs')
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, 'errors.log'), 'a', encoding='utf-8') as f:
            f.write(f"--- UI executor error at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            traceback.print_exc(file=f)
            f.write("\n")
    except Exception:
        pass


class UiExecutor:
    """单例：管理后台到主线程的 UI 更新队列"""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, root=None):
        if cls._instance is None:
            if root is None:
                raise ValueError("UiExecutor 首次初始化需要传入 root 窗口")
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init(root)
        return cls._instance

    def _init(self, root):
        self.root = root
        self._queue = queue.Queue()
        self._after_id = None
        self._running = True
        self._poll()

    def submit(self, func, *args, **kwargs):
        """从任意线程提交一个函数到主线程执行"""
        try:
            self._queue.put((func, args, kwargs))
        except Exception:
            pass

    def _poll(self):
        """主线程中消费队列并执行 UI 刷新"""
        try:
            if not self._running or not self.root or not self.root.winfo_exists():
                return
        except tk.TclError:
            return

        try:
            for _ in range(20):
                func, args, kwargs = self._queue.get_nowait()
                try:
                    func(*args, **kwargs)
                except Exception:
                    _log_executor_error()
        except queue.Empty:
            pass

        try:
            self._after_id = self.root.after(100, self._poll)
        except Exception:
            self._after_id = None

    def stop(self):
        """停止轮询，通常在窗口关闭时调用"""
        self._running = False
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
