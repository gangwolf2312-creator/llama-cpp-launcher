"""
主窗口
整合左右面板、状态栏、PanedWindow可拖拽布局
"""
import tkinter as tk
from tkinter import ttk
import ttkbootstrap as ttkb
import os
import sys

from ui.left_panel import LeftPanel
from ui.right_panel import RightPanel
from core.config_manager import ConfigManager, get_config_mgr
from core.param_manager import get_param_mgr
from core.server_manager import ServerManager
from core.hardware_monitor import HardwareMonitor
from core.ui_executor import UiExecutor
from core.logger import get_logger


class MainWindow:
    """主窗口"""

    def __init__(self, app_dir=None, resource_dir=None):
        # 应用目录 (exe所在目录，配置文件写入这里)
        if app_dir is None:
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.app_dir = app_dir

        # 资源目录 (打包资源读取目录，单文件模式下是 _MEIPASS 临时目录)
        if resource_dir is None:
            resource_dir = app_dir
        self.resource_dir = resource_dir

        # 初始化核心组件
        # 配置管理器：配置文件保存在 app_dir (可写目录)
        self.config = get_config_mgr(app_dir)
        # 参数管理器：从 resource_dir 读取 llama-params.json
        params_json = os.path.join(resource_dir, "llama-params.json")
        self.param_mgr = get_param_mgr(params_json if os.path.exists(params_json) else None)
        self.logger = get_logger(app_dir)
        self.server_mgr = ServerManager(self.config, self.param_mgr)
        self.hw_monitor = HardwareMonitor(interval=1.0)

        # 创建窗口
        theme = self.config.get("app.theme", "darkly")
        self.root = ttkb.Window(themename=theme)
        self.root.title("Llama.cpp Launcher v3.0")

        # 窗口大小和位置
        width = self.config.get("ui.window_width", 1400)
        height = self.config.get("ui.window_height", 900)
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = (screen_w - width) // 2
        y = (screen_h - height) // 2
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(1000, 700)

        # 初始化统一的 UI 调度器
        UiExecutor(self.root)

        # 窗口关闭处理
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 设置窗口图标 (从资源目录读取)
        icon_path = os.path.join(self.resource_dir, "assets", "icon.ico")
        if not os.path.exists(icon_path):
            # 回退：从 app_dir 读取
            icon_path = os.path.join(self.app_dir, "assets", "icon.ico")
        if os.path.exists(icon_path):
            try:
                self.root.iconbitmap(icon_path)
            except Exception:
                pass

        self.logger.info("="*50)
        self.logger.info("Llama.cpp Launcher v3.0 \u542f\u52a8")
        self.logger.info(f"\u5e94\u7528\u76ee\u5f55: {self.app_dir}")
        self.logger.info(f"\u8d44\u6e90\u76ee\u5f55: {self.resource_dir}")
        self.logger.info("="*50)

        # 构建UI
        self._build_ui()

        # 启动硬件监控
        self.hw_monitor.add_callback(self._on_hw_update)
        self.hw_monitor.start()

        # 绑定窗口大小变化事件
        self.root.bind('<Configure>', self._on_window_configure)
        self._resize_timer = None

    def _build_ui(self):
        """构建主界面"""
        # 主PanedWindow (左右分割)
        self.main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        self.main_paned.pack(fill=tk.BOTH, expand=True)

        # 左侧面板
        self.left_panel = LeftPanel(
            self.main_paned, self.config, self.param_mgr, self.logger,
            on_param_change=self._on_param_change
        )
        left_frame = self.left_panel.get_frame()

        # 右侧容器 (内部再分割: Notebook + 日志)
        right_container = ttk.Frame(self.main_paned)

        # 右侧Notebook
        self.right_panel = RightPanel(
            right_container, self.config, self.server_mgr, self.logger, self.param_mgr
        )
        self.right_panel.get_widget().pack(fill=tk.BOTH, expand=True)
        self.right_panel.set_param_change_callback(self._on_param_change)

        # 添加到PanedWindow
        left_width = self.config.get("ui.splitter_distance_left", 420)
        self.main_paned.add(left_frame, weight=1)
        self.main_paned.add(right_container, weight=3)

        # 设置分隔条位置
        self.root.after(100, lambda: self._set_sash_position(left_width))

        # 底部状态栏
        self._build_status_bar()
        self._poll_service_status()

    def _set_sash_position(self, pos):
        """设置分隔条位置"""
        try:
            self.main_paned.sashpos(0, pos)
        except Exception:
            pass

    def _build_status_bar(self):
        """构建底部状态栏"""
        self.status_bar = ttk.Frame(self.root, relief=tk.SUNKEN, padding=(4, 2))
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        # 服务状态
        service_frame = ttk.Frame(self.status_bar)
        service_frame.pack(side=tk.LEFT, padx=8)
        ttk.Label(service_frame, text="服务:", font=("Consolas", 9)).pack(side=tk.LEFT)
        self.service_indicator = tk.Canvas(service_frame, width=12, height=12, highlightthickness=0)
        self.service_indicator.pack(side=tk.LEFT, padx=2)
        self.service_indicator_id = self.service_indicator.create_oval(2, 2, 10, 10, fill='red')
        self.service_label = ttk.Label(service_frame, text="已停止", font=("Consolas", 9))
        self.service_label.pack(side=tk.LEFT)

        # CPU
        cpu_frame = ttk.Frame(self.status_bar)
        cpu_frame.pack(side=tk.LEFT, padx=8)
        ttk.Label(cpu_frame, text="CPU:", font=("Consolas", 9)).pack(side=tk.LEFT)
        self.cpu_bar = ttk.Progressbar(cpu_frame, length=80, maximum=100, mode='determinate')
        self.cpu_bar.pack(side=tk.LEFT, padx=2)
        self.cpu_label = ttk.Label(cpu_frame, text="0%", width=5, font=("Consolas", 9))
        self.cpu_label.pack(side=tk.LEFT)

        # 内存
        mem_frame = ttk.Frame(self.status_bar)
        mem_frame.pack(side=tk.LEFT, padx=8)
        ttk.Label(mem_frame, text="\u5185\u5b58:", font=("Consolas", 9)).pack(side=tk.LEFT)
        self.mem_bar = ttk.Progressbar(mem_frame, length=80, maximum=100, mode='determinate')
        self.mem_bar.pack(side=tk.LEFT, padx=2)
        self.mem_label = ttk.Label(mem_frame, text="0/0GB", width=12, font=("Consolas", 9))
        self.mem_label.pack(side=tk.LEFT)

        # GPU
        gpu_frame = ttk.Frame(self.status_bar)
        gpu_frame.pack(side=tk.LEFT, padx=8)
        ttk.Label(gpu_frame, text="GPU:", font=("Consolas", 9)).pack(side=tk.LEFT)
        self.gpu_bar = ttk.Progressbar(gpu_frame, length=80, maximum=100, mode='determinate')
        self.gpu_bar.pack(side=tk.LEFT, padx=2)
        self.gpu_label = ttk.Label(gpu_frame, text="0%", width=5, font=("Consolas", 9))
        self.gpu_label.pack(side=tk.LEFT)

        # GPU显存
        self.vram_label = ttk.Label(self.status_bar, text="VRAM: --", font=("Consolas", 9))
        self.vram_label.pack(side=tk.LEFT, padx=8)

        # 右侧信息
        self.info_label = ttk.Label(self.status_bar, text="Ready", font=("Consolas", 9))
        self.info_label.pack(side=tk.RIGHT, padx=8)

    def _update_service_status(self, running):
        """更新服务状态指示灯"""
        color = 'green' if running else 'red'
        if running:
            text = '运行中'
        else:
            text = '已停止'
        try:
            self.service_indicator.itemconfig(self.service_indicator_id, fill=color)
            self.service_label.configure(text=text)
        except Exception:
            pass

    def _poll_service_status(self):
        """定时轮询服务状态并刷新指示灯"""
        if hasattr(self, 'server_mgr') and self.server_mgr:
            self._update_service_status(self.server_mgr.is_running())
        if self.root and self.root.winfo_exists():
            self.root.after(1000, self._poll_service_status)

    def _on_hw_update(self, data):
        """硬件监控数据更新回调（后台线程，只提交到 UI 调度器）"""
        UiExecutor().submit(self._update_hw_ui, data)

    def _update_hw_ui(self, data):
        """硬件监控 UI 刷新（必须在主线程调用）"""
        try:
            self.cpu_bar['value'] = data['cpu_percent']
            self.cpu_label.configure(text=f"{data['cpu_percent']:.0f}%")

            self.mem_bar['value'] = data['memory_percent']
            self.mem_label.configure(
                text=f"{data['memory_used_gb']:.1f}/{data['memory_total_gb']:.0f}GB"
            )

            self.gpu_bar['value'] = min(data['gpu_percent'], 100)
            self.gpu_label.configure(text=f"{data['gpu_percent']:.0f}%")

            # Format VRAM display: Dedicated + Shared for AMD iGPU
            total_mb = data['gpu_vram_total_mb']
            shared_mb = data.get('gpu_vram_shared_mb', 0)
            used_mb = data['gpu_vram_used_mb']
            gpu_name = data.get('gpu_name', '')

            if total_mb > 0:
                # Convert to GB if > 1024 MB
                if total_mb >= 1024:
                    if shared_mb > 0:
                        text = (
                            f"VRAM: {used_mb/1024:.1f}/"
                            f"{total_mb/1024:.0f}GB+{shared_mb/1024:.0f}GBs"
                        )
                    else:
                        text = f"VRAM: {used_mb/1024:.1f}/{total_mb/1024:.0f}GB"
                else:
                    text = f"VRAM: {used_mb:.0f}/{total_mb:.0f}MB"

                # Append GPU name if short enough
                if gpu_name:
                    short_name = gpu_name.replace("AMD ", "").replace("Radeon ", "")
                    if len(short_name) > 20:
                        short_name = short_name[:17] + "..."
                    text += f" | {short_name}"

                self.vram_label.configure(text=text)
            else:
                self.vram_label.configure(text="VRAM: --")

        except Exception:
            pass

    def _on_param_change(self, name, enabled, value):
        """参数变更回调"""
        if name in ('__hardware_preset__', '__model_params__'):
            if hasattr(self, 'left_panel') and self.left_panel:
                self.left_panel.refresh_params()
        if hasattr(self, 'right_panel') and self.right_panel:
            self.right_panel.refresh()

    def _on_window_configure(self, event):
        """窗口大小变化处理"""
        if event.widget != self.root:
            return
        # 防抖保存
        if self._resize_timer:
            self.root.after_cancel(self._resize_timer)
        self._resize_timer = self.root.after(500, self._save_window_size)

    def _save_window_size(self):
        """保存窗口大小和分隔条位置"""
        try:
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            if w > 200 and h > 200:
                self.config.set("ui.window_width", w)
                self.config.set("ui.window_height", h)

            try:
                sash = self.main_paned.sashpos(0)
                if sash > 100:
                    self.config.set("ui.splitter_distance_left", sash)
            except Exception:
                pass

            self.config.save()
        except Exception:
            pass

    def _on_close(self):
        """窗口关闭处理：无条件停止服务并保存配置，确保窗口能关闭"""
        try:
            self.logger.info("\u5e94\u7528\u5173\u95ed\u4e2d...")

            # 停止运行状态监控（先停监控，再停服务）
            try:
                if self.right_panel:
                    self.right_panel.stop()
            except Exception as e:
                self.logger.error(f"\u505c\u6b62\u72b6\u6001\u76d1\u63a7\u5931\u8d25: {e}")

            # 停止服务（不再询问，直接停止，避免残留进程）
            if self.server_mgr.is_running():
                try:
                    self.server_mgr.stop()
                except Exception as e:
                    self.logger.error(f"\u505c\u6b62\u670d\u52a1\u5931\u8d25: {e}")

            # 停止监控（缺失 pywin32 时不应阻止窗口关闭）
            try:
                self.hw_monitor.stop()
            except Exception as e:
                self.logger.error(f"\u505c\u6b62\u786c\u4ef6\u76d1\u63a7\u5931\u8d25: {e}")

            # 停止 UI 调度器，避免残留 after
            try:
                UiExecutor().stop()
            except Exception as e:
                self.logger.error(f"\u505c\u6b62 UI \u8c03\u5ea6\u5668\u5931\u8d25: {e}")

            # 保存配置
            try:
                self._save_window_size()
                self.config.save()
            except Exception as e:
                self.logger.error(f"\u4fdd\u5b58\u914d\u7f6e\u5931\u8d25: {e}")
        finally:
            # 确保主窗口始终能关闭
            self.root.destroy()

    def run(self):
        """运行主循环"""
        self.root.mainloop()
