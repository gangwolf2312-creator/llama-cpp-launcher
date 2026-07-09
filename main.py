#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Llama.cpp Launcher v3.0
本地 AI 模型启动器 - 入口文件
支持 PyInstaller 单文件/单目录打包模式
"""
import sys
import os
import time
import traceback
import threading


def get_app_dir():
    """
    获取应用目录 - 兼容开发模式和 PyInstaller 打包模式
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包模式
        # --onefile: _MEIPASS 指向解压后的临时目录（只读资源）
        # --onedir: _MEIPASS 指向包含 exe 的目录
        return os.path.dirname(sys.executable)
    else:
        # 开发模式
        return os.path.dirname(os.path.abspath(__file__))


def get_resource_dir():
    """
    获取资源目录 - 打包文件所在位置（只读）
    """
    if getattr(sys, 'frozen', False):
        if hasattr(sys, '_MEIPASS'):
            return sys._MEIPASS  # PyInstaller 解压目录
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def setup_path():
    """确保模块导入路径正确"""
    app_dir = get_app_dir()
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
    # PyInstaller 模式需要将 _MEIPASS 加入路径以导入打包的模块
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        meipass = sys._MEIPASS
        if meipass not in sys.path:
            sys.path.insert(0, meipass)
    return app_dir


def _log_unhandled_exception(exc_type, exc_value, exc_traceback):
    """记录未捕获异常到 logs/errors.log，方便诊断 bat/命令行里的报错"""
    try:
        app_dir = get_app_dir()
        log_dir = os.path.join(app_dir, 'logs')
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, 'errors.log')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"--- Unhandled exception at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            traceback.print_exception(exc_type, exc_value, exc_traceback, file=f)
            f.write("\n")
    except Exception:
        pass
    # 仍然把异常抛给默认 hook，让命令行窗口显示
    sys.__excepthook__(exc_type, exc_value, exc_traceback)


sys.excepthook = _log_unhandled_exception

# 捕获后台线程未处理异常
threading.excepthook = lambda args: _log_unhandled_exception(args.exc_type, args.exc_value, args.exc_traceback)


def main():
    app_dir = setup_path()
    resource_dir = get_resource_dir()

    # 检查依赖（静默模式，不打印到控制台以免弹出控制台窗口）
    missing_deps = []
    try:
        import ttkbootstrap
    except ImportError:
        missing_deps.append("ttkbootstrap")

    try:
        import psutil
    except ImportError:
        missing_deps.append("psutil")

    try:
        import win32pdh
    except ImportError:
        pass  # pywin32 可选

    # 启动应用
    from ui.main_window import MainWindow

    app = MainWindow(app_dir=app_dir, resource_dir=resource_dir)
    app.run()


if __name__ == "__main__":
    main()
