"""右侧Tab面板
包含: 启动配置Tab、批量上下文测速Tab、API测试Tab、启动日志Tab、文件路径Tab、运行状态Tab
"""
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
from pathlib import Path
from utils.helpers import scan_models, detect_model_info, browse_file, browse_directory, clamp_ctx_to_gears
from ttkbootstrap.widgets import ToolTip
import threading
import time
import json
import urllib.request
import urllib.error
from utils.performance_presets import get_recommended_params, format_hardware_summary
from core.hardware_monitor import HardwareMonitor
from core.status_monitor import StatusMonitor
from core.ui_executor import UiExecutor
from ui.status_tab import StatusTab


class StartConfigTab:
    """启动配置 Tab"""

    def __init__(self, parent, config_mgr, server_mgr, logger):
        self.frame = ttk.Frame(parent)
        self.config = config_mgr
        self.server_mgr = server_mgr
        self.logger = logger
        self._param_change_callback = None
        self._preview_timer = None
        self._selecting_model = False
        self.energy_phase = 0
        self.energy_running = False
        self.energy_after_id = None

        self._build_ui()
        self._update_model_display()
        self._update_command_preview()
        self._update_buttons()

        self.server_mgr.set_callback('on_start', self._on_server_start)
        self.server_mgr.set_callback('on_stop', self._on_server_stop)
        self.server_mgr.set_callback('on_error', self._on_server_error)

    def _build_ui(self):
        """构建启动配置 UI"""
        canvas = tk.Canvas(self.frame, highlightthickness=0)
        vsb = ttk.Scrollbar(self.frame, orient="vertical", command=canvas.yview)
        scrollable = ttk.Frame(canvas)
        scrollable.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.scrollable = scrollable

        # 模型选择
        self._build_model_section(scrollable)

        # 硬件信息
        hw_frame = ttk.LabelFrame(scrollable, text="硬件信息", padding=6)
        hw_frame.pack(fill=tk.X, padx=4, pady=4)
        self.hw_label = ttk.Label(hw_frame, text="未检测到硬件")
        self.hw_label.pack(anchor='w')
        self.rec_btn = ttk.Button(hw_frame, text="应用推荐参数", command=self._apply_recommended)
        self.rec_btn.pack(anchor='w', pady=4)

        # 命令预览
        cmd_frame = ttk.LabelFrame(scrollable, text="命令预览", padding=6)
        cmd_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.cmd_text = tk.Text(cmd_frame, height=8, wrap=tk.WORD, font=("Consolas", 9))
        self.cmd_text.pack(fill=tk.BOTH, expand=True)

        # 按钮
        btn_frame = ttk.Frame(scrollable)
        btn_frame.pack(fill=tk.X, padx=4, pady=4)
        self.start_btn = ttk.Button(btn_frame, text="启动服务", command=self._start_server)
        self.start_btn.pack(side=tk.LEFT, padx=2)
        self.stop_btn = ttk.Button(btn_frame, text="停止服务", command=self._stop_server)
        self.stop_btn.pack(side=tk.LEFT, padx=2)
        self.refresh_btn = ttk.Button(btn_frame, text="刷新预览", command=self._update_command_preview)
        self.refresh_btn.pack(side=tk.LEFT, padx=2)

        # 启动进度能量格子
        self.energy_frame = ttk.LabelFrame(scrollable, text="启动进度", padding=6)
        self.energy_canvas = tk.Canvas(self.energy_frame, width=210, height=34, highlightthickness=0, bg='#212529')
        self.energy_canvas.pack(anchor='w')
        self.energy_label = ttk.Label(self.energy_frame, text="")
        self.energy_label.pack(anchor='w', pady=(2, 0))
        self.energy_segments = []
        seg_w, seg_h, gap = 16, 22, 4
        for i in range(10):
            x1 = 5 + i * (seg_w + gap)
            y1 = 5
            x2 = x1 + seg_w
            y2 = y1 + seg_h
            rect = self.energy_canvas.create_rectangle(x1, y1, x2, y2, outline='#495057', fill='#212529', width=1)
            self.energy_segments.append(rect)
        self.energy_frame.pack(fill=tk.X, padx=4, pady=4)
        self.energy_frame.pack_forget()

    def _build_model_section(self, parent):
        """模型选择区：直接把模型库列表嵌入，不再弹窗"""
        model_frame = ttk.LabelFrame(parent, text="模型选择", padding=6)
        model_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self.model_var = tk.StringVar(value=self.config.get("server.last_model", ""))
        self.model_display = ttk.Label(model_frame, textvariable=self.model_var, wraplength=600)
        self.model_display.pack(fill=tk.X, padx=2, pady=2)

        btn_frame = ttk.Frame(model_frame)
        btn_frame.pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="浏览模型", command=self._browse_model).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="刷新列表", command=self._refresh_model_list).pack(side=tk.LEFT, padx=2)

        # 模型库列表
        list_frame = ttk.Frame(model_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=2)
        self.model_listbox = tk.Listbox(list_frame, height=8, exportselection=False)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.model_listbox.yview)
        self.model_listbox.configure(yscrollcommand=scrollbar.set)
        self.model_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.model_listbox.bind('<<ListboxSelect>>', self._on_model_list_select)

        self.model_info_var = tk.StringVar(value="未选择模型")
        ttk.Label(model_frame, textvariable=self.model_info_var, foreground='#adb5bd').pack(anchor='w', pady=2)

        self._models = []
        self._ignore_list_select = False
        self._refresh_model_list()

    def _browse_model(self):
        """浏览选择模型文件"""
        initial = self.config.get("paths.last_model_dir", "")
        path = browse_file("选择模型", [("GGUF", "*.gguf;*.GGUF"), ("All", "*.*")], initialdir=initial)
        if path:
            self.config.set("paths.last_model_dir", str(Path(path).parent))
            self.update_selected_model(path)

    def _refresh_model_list(self):
        """刷新模型库列表"""
        self.model_listbox.delete(0, tk.END)
        self._models = []
        lib = self.config.get("paths.model_library", "")
        if not lib:
            self.model_listbox.insert(tk.END, "模型库未配置")
            return
        try:
            models = scan_models(lib)
        except Exception as e:
            self.logger.error(f"扫描模型库失败: {e}")
            self.model_listbox.insert(tk.END, f"扫描失败: {e}")
            return
        self._models = models
        for m in models:
            self.model_listbox.insert(tk.END, f"{m['name']} ({m['size_gb']:.1f}GB) [{m['category']}]")
        # 高亮当前模型；暂时忽略选择事件，避免程序刷新触发循环检测
        self._ignore_list_select = True
        try:
            current = self.model_var.get()
            for i, m in enumerate(models):
                if str(m['path']) == current or m['path'] == current:
                    self.model_listbox.selection_set(i)
                    self.model_listbox.see(i)
                    break
        finally:
            self.frame.after_idle(lambda: setattr(self, '_ignore_list_select', False))

    def _on_model_list_select(self, event=None):
        """列表选中模型后启动后台检测"""
        if self._ignore_list_select:
            return
        sel = self.model_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._models):
            return
        path = self._models[idx]['path']
        self.update_selected_model(path)

    def update_selected_model(self, model_path):
        """开始模型选择流程（后台检测，主线程应用）"""
        if not model_path or not Path(model_path).exists():
            self.model_var.set(model_path or "")
            self.model_info_var.set("未选择模型")
            return
        self.model_var.set(model_path)
        self.config.set("server.last_model", model_path)
        self.config.set("paths.last_model", model_path)
        # 避免连点触发多个后台线程
        if getattr(self, '_selecting_model', False):
            return
        self._selecting_model = True
        threading.Thread(target=self._select_model_async, args=(model_path,), daemon=True).start()

    def _select_model_async(self, model_path):
        """后台线程：读取 GGUF 元数据和历史参数"""
        try:
            vision_path = self.config.get("paths.mmproj", "")
            mtp_path = self.config.get("paths.mtp_model", "")
            info = detect_model_info(model_path, vision_path, mtp_path)
            has_saved = self.config.load_model_params(model_path)
        except Exception as e:
            self.logger.error(f"检测模型信息失败: {e}")
            info = {}
            has_saved = False
        UiExecutor().submit(self._apply_model_done, model_path, info, has_saved)

    def _apply_model_done(self, model_path, info, has_saved):
        """主线程：应用模型检测结果和历史/推荐参数"""
        self._selecting_model = False
        self.model_var.set(model_path)
        self.config.set("server.last_model", model_path)
        self.config.set("paths.last_model", model_path)

        # 更新信息标签
        model_type = '稠密' if not info.get('is_moe') else 'MoE'
        text = (
            f"类型: {model_type} | "
            f"推荐上下文: {info.get('recommended_ctx', 0)} | "
            f"视觉: {'是' if info.get('is_vision') else '否'} | "
            f"MTP: {'是' if info.get('is_mtp') else '否'}"
        )
        self.model_info_var.set(text)

        # 同步 ctx-size，并对齐到 UI 允许的档位（32k/64k/128k/256k）
        try:
            ctx = int(info.get('recommended_ctx', 0))
            if ctx > 0:
                ctx = clamp_ctx_to_gears(ctx)
                self.config.set_param_value('ctx-size', ctx)
        except Exception as e:
            self.logger.warning(f"同步 ctx-size 失败: {e}")

        # 视觉模型：自动识别当前模型目录下的 mmproj，非视觉模型则清空 vision_model，
        # 避免上一个模型的 mmproj 被错误加载到当前模型
        try:
            if info.get('is_vision') and info.get('mmproj_path'):
                self.config.set("paths.vision_model", info['mmproj_path'])
            else:
                self.config.set("paths.vision_model", "")
        except Exception as e:
            self.logger.warning(f"同步 vision_model 失败: {e}")

        # 应用历史或推荐参数
        if has_saved:
            self._apply_saved_model_params(model_path, already_loaded=True)
        else:
            self._apply_recommended(silent=True)

        self._update_command_preview()
        self.config.save()
        self._refresh_model_list()

    def _update_model_display(self):
        """刷新模型显示和硬件摘要"""
        try:
            gpu_info = HardwareMonitor.get_gpu_info_static()
            sys_info = HardwareMonitor.get_system_info()
            self.hw_label.configure(text=format_hardware_summary(gpu_info, sys_info))
        except Exception as e:
            self.hw_label.configure(text=f"硬件信息获取失败: {e}")

        model_path = self.model_var.get()
        if model_path and Path(model_path).exists():
            self.update_selected_model(model_path)
        else:
            self.model_info_var.set("未选择模型")

    def _apply_saved_model_params(self, model_path, already_loaded=False):
        """尝试加载该模型的历史成功参数"""
        loaded = already_loaded or self.config.load_model_params(model_path)
        if not loaded:
            return False
        self.logger.info(f"已加载模型历史参数: {model_path}")
        if self._param_change_callback:
            self._param_change_callback('__model_params__', True, None)
        self._update_command_preview()
        return True

    def _apply_recommended(self, silent=False):
        """应用硬件/模型推荐参数"""
        model_path = self.model_var.get()
        if not model_path or not Path(model_path).exists():
            if not silent:
                messagebox.showwarning("未选择模型", "请先选择一个模型。")
            return

        try:
            gpu_info = HardwareMonitor.get_gpu_info_static()
            sys_info = HardwareMonitor.get_system_info()
            preset, params = get_recommended_params(model_path, gpu_info, sys_info)

            self.config.set_params_enabled_all({k: True for k in params.keys()})
            self.config.set_param_values_all(params)
            self.config.save()

            if self._param_change_callback:
                self._param_change_callback('__hardware_preset__', True, None)

            self._update_command_preview()
            if not silent:
                messagebox.showinfo("推荐参数", f"已应用: {preset.get('name', '')}\n{preset.get('description', '')}")
        except Exception as e:
            self.logger.error(f"应用推荐参数失败: {e}")
            if not silent:
                messagebox.showerror("错误", f"应用推荐参数失败: {e}")

    def _update_command_preview(self):
        """刷新命令预览（防抖，避免连续参数变更反复构建命令）"""
        if self._preview_timer:
            self.frame.after_cancel(self._preview_timer)
        self._preview_timer = self.frame.after(150, self._do_update_command_preview)

    def _do_update_command_preview(self):
        """实际构建并显示命令"""
        self._preview_timer = None
        try:
            cmd = self.server_mgr.build_command()
            self.cmd_text.delete('1.0', tk.END)
            self.cmd_text.insert('1.0', ' '.join(str(x) for x in cmd))
        except Exception as e:
            self.cmd_text.delete('1.0', tk.END)
            self.cmd_text.insert('1.0', f"无法生成命令: {e}")

    def _start_server(self):
        """启动服务（放到后台线程，避免卡住主界面）"""
        self.start_btn.configure(state='disabled')
        self.stop_btn.configure(state='disabled')
        self._start_energy_grid()

        def do_start():
            ok, msg = self.server_mgr.start()
            if ok:
                model_path = self.model_var.get()
                if model_path:
                    self.config.save_model_params(model_path)
            else:
                self._after(lambda: (
                    self._hide_energy_grid(),
                    messagebox.showerror("启动失败", msg)
                ))
        threading.Thread(target=do_start, daemon=True).start()

    def _stop_server(self):
        """停止服务"""
        self.server_mgr.stop()
        self._hide_energy_grid()

    def _update_buttons(self):
        """根据服务状态更新按钮"""
        running = self.server_mgr.is_running()
        self.start_btn.configure(state='disabled' if running else 'normal')
        self.stop_btn.configure(state='normal' if running else 'disabled')

    def _after(self, func):
        """后台线程提交 UI 更新任务，不直接调用 tkinter"""
        UiExecutor().submit(func)

    def _on_server_start(self, cmd):
        """服务启动回调（可能来自后台线程，统一提交到 UI 调度器）"""
        self._after(lambda: (
            self._update_buttons(),
            self._update_command_preview(),
            self._start_energy_grid()
        ))
        # 后台等待服务就绪，就绪后把能量格填满
        threading.Thread(target=self._poll_server_ready, daemon=True).start()

    def _on_server_stop(self):
        """服务停止回调（可能来自后台线程，统一提交到 UI 调度器）"""
        self._after(lambda: (self._update_buttons(), self._hide_energy_grid()))

    def _on_server_error(self, msg):
        """服务启动错误回调"""
        self._after(lambda: (
            self._stop_energy_grid(error=True, message=f"启动错误: {msg}"),
            self._update_buttons()
        ))

    def _get_api_host_port(self):
        """获取当前 host/port"""
        host = self.config.get_param_value('host', '127.0.0.1')
        port = self.config.get_param_value('port', 8080)
        return host, port

    def _poll_server_ready(self):
        """后台轮询 /health，直到服务就绪或进程停止"""
        host, port = self._get_api_host_port()
        while self.server_mgr.is_running():
            try:
                urllib.request.urlopen(f"http://{host}:{port}/health", timeout=2)
                self._after(self._stop_energy_grid)
                return
            except Exception:
                time.sleep(0.5)
        # 进程已停止但未就绪，隐藏能量格
        self._after(self._hide_energy_grid)

    def _start_energy_grid(self):
        """显示并开始能量格子动画"""
        if self.energy_running:
            return
        self.energy_frame.pack(fill=tk.X, padx=4, pady=4)
        self.energy_label.configure(text="启动中...")
        self.energy_phase = 0
        self.energy_running = True
        self._clear_energy_grid()
        self._animate_energy_grid()

    def _animate_energy_grid(self):
        """能量格子填充动画"""
        if not self.energy_running:
            return
        for i, rect in enumerate(self.energy_segments):
            fill = '#0dcaf0' if i < self.energy_phase else '#212529'
            self.energy_canvas.itemconfig(rect, fill=fill)
        self.energy_phase = (self.energy_phase + 1) % (len(self.energy_segments) + 1)
        self.energy_after_id = self.frame.after(150, self._animate_energy_grid)

    def _stop_energy_grid(self, error=False, message=None):
        """停止动画并显示最终状态"""
        self.energy_running = False
        if self.energy_after_id:
            self.frame.after_cancel(self.energy_after_id)
            self.energy_after_id = None
        if error:
            fill = '#dc3545'
            text = message or "启动失败"
        else:
            fill = '#0dcaf0'
            text = "服务就绪"
        for rect in self.energy_segments:
            self.energy_canvas.itemconfig(rect, fill=fill)
        self.energy_label.configure(text=text)
        self.energy_frame.pack(fill=tk.X, padx=4, pady=4)

    def _hide_energy_grid(self):
        """隐藏能量格子"""
        self.energy_running = False
        if self.energy_after_id:
            self.frame.after_cancel(self.energy_after_id)
            self.energy_after_id = None
        self.energy_frame.pack_forget()
        self._clear_energy_grid()

    def _clear_energy_grid(self):
        """清空能量格子填充"""
        for rect in self.energy_segments:
            self.energy_canvas.itemconfig(rect, fill='#212529')

    def set_param_change_callback(self, callback):
        """设置参数变化回调"""
        self._param_change_callback = callback

    def refresh(self):
        """刷新启动配置Tab：只更新命令预览和按钮状态，不重新检测模型"""
        self._update_command_preview()
        self._update_buttons()


class BenchmarkTab:
    """批量上下文测速 Tab"""

    def __init__(self, parent, config_mgr, server_mgr, logger, param_mgr=None):
        self.frame = ttk.Frame(parent)
        self.config = config_mgr
        self.server_mgr = server_mgr
        self.logger = logger
        self.param_mgr = param_mgr

        self._thread = None
        self._stop_event = threading.Event()
        self._last_stats = {}
        self._stats_event = threading.Event()
        self._lock = threading.Lock()

        self._build_ui()
        self.server_mgr.set_callback('on_token_stats', self._on_token_stats)

    def _build_ui(self):
        """构建测速 UI"""
        # 测试上下文选择
        ctrl_frame = ttk.LabelFrame(self.frame, text="测试上下文", padding=6)
        ctrl_frame.pack(fill=tk.X, padx=4, pady=4)

        self.ctx_vars = {}
        # 默认测试上下文从 ctx-size 参数定义读取，保持与左侧参数档位一致
        default_ctx = [32768, 65536, 131072, 262144]
        if self.param_mgr:
            param_def = self.param_mgr.get_param_def('ctx-size')
            if param_def and param_def.get('values'):
                try:
                    default_ctx = [int(v) for v in param_def['values']]
                except Exception:
                    pass
        for ctx in default_ctx:
            var = tk.BooleanVar(value=True)
            self.ctx_vars[ctx] = var
            ttk.Checkbutton(ctrl_frame, text=f"{ctx // 1024}K", variable=var).pack(side=tk.LEFT, padx=4)

        self.run_btn = ttk.Button(ctrl_frame, text="开始测速", command=self._run_benchmark)
        self.run_btn.pack(side=tk.LEFT, padx=4)
        self.stop_btn = ttk.Button(ctrl_frame, text="停止", command=self._stop_benchmark, state='disabled')
        self.stop_btn.pack(side=tk.LEFT, padx=4)

        # 进度条
        self.progress = ttk.Progressbar(self.frame, maximum=100, mode='determinate')
        self.progress.pack(fill=tk.X, padx=4, pady=4)

        # 结果表格
        table_frame = ttk.Frame(self.frame)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        cols = ('ctx', 'prompt', 'gen', 'status')
        self.tree = ttk.Treeview(table_frame, columns=cols, show='headings')
        for c, text in [('ctx', '上下文'), ('prompt', 'Prompt(t/s)'), ('gen', 'Gen(t/s)'), ('status', '状态')]:
            self.tree.heading(c, text=text)
            self.tree.column(c, width=100, anchor='center')
        self.tree.column('ctx', width=80)
        self.tree.column('status', width=120)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(table_frame, orient='vertical', command=self.tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self.frame, textvariable=self.status_var).pack(anchor='w', padx=4)

    def _on_token_stats(self, stats):
        """接收日志解析出的 Token 统计"""
        with self._lock:
            self._last_stats.update(stats)
            if any(stats.get(k) for k in ('prompt_eval_rate', 'eval_rate', 'slot_prompt', 'slot_generation')):
                self._stats_event.set()

    def _run_benchmark(self):
        """启动测速线程"""
        self._stop_event.clear()
        self.run_btn.configure(state='disabled')
        self.stop_btn.configure(state='normal')
        self._thread = threading.Thread(target=self._benchmark_loop, daemon=True)
        self._thread.start()

    def _stop_benchmark(self):
        """请求停止测速"""
        self._stop_event.set()
        self._set_status("正在停止...")

    def _benchmark_loop(self):
        """批量测速主循环"""
        selected = sorted([ctx for ctx, var in self.ctx_vars.items() if var.get()])
        if not selected:
            self._set_status("请至少选择一个上下文档位")
            self._after(lambda: self.run_btn.configure(state='normal'))
            self._after(lambda: self.stop_btn.configure(state='disabled'))
            return

        # 记住原始状态
        original_ctx = self.config.get_param_value('ctx-size')
        original_running = self.server_mgr.is_running()
        total = len(selected)

        self._after(lambda: self.tree.delete(*self.tree.get_children()))

        for i, ctx in enumerate(selected):
            if self._stop_event.is_set():
                self._set_status("已中止")
                break

            self._set_progress(i / total * 100)
            self._set_status(f"正在测试 {ctx} 上下文...")
            self._insert_result(ctx, None, None, "运行中")

            # 修改 ctx-size
            self.config.set_param_value('ctx-size', ctx)

            # 确保服务以新的 ctx-size 启动/重启
            if not self.server_mgr.is_running():
                ok, msg = self.server_mgr.start()
                if not ok:
                    self._insert_result(ctx, None, None, f"启动失败: {msg}")
                    continue
            else:
                self.server_mgr.restart()

            if not self._wait_for_server(timeout=60):
                self._insert_result(ctx, None, None, "服务未就绪")
                continue

            try:
                host, port = self._get_api_host_port()
                self._reset_stats()
                measured = self._send_benchmark_request(host, port, ctx)
            except Exception as e:
                self._insert_result(ctx, None, None, f"请求失败: {e}")
                continue

            # 非流式请求返回后，日志可能还没 flush；流式请求已结束，再等一下日志
            time.sleep(1)

            stats = self._wait_for_token_stats(timeout=30)
            log_prompt = stats.get('prompt_eval_rate') if stats else None
            log_gen = stats.get('eval_rate') if stats else None

            if log_prompt or log_gen:
                prompt_rate = log_prompt or 0
                gen_rate = log_gen or 0
                self._insert_result(ctx, prompt_rate, gen_rate, "完成")
            elif measured and (measured.get('prompt_eval_rate') or measured.get('eval_rate')):
                prompt_rate = measured.get('prompt_eval_rate', 0)
                gen_rate = measured.get('eval_rate', 0)
                self._insert_result(ctx, prompt_rate, gen_rate, "完成")
            else:
                self._insert_result(ctx, None, None, "日志未解析到速度")
                self._dump_server_logs(ctx)

        self._set_progress(100)
        if not self._stop_event.is_set():
            self._set_status("测试完成")

        # 恢复原始 ctx-size
        if original_ctx is not None:
            self.config.set_param_value('ctx-size', original_ctx)
        elif self.param_mgr:
            default_ctx = self.param_mgr.get_param_def('ctx-size')
            if default_ctx:
                self.config.set_param_value('ctx-size', default_ctx.get('default', 4096))

        # 恢复原始运行状态
        if original_running:
            self.server_mgr.restart()
        else:
            self.server_mgr.stop()

        self._after(lambda: self.run_btn.configure(state='normal'))
        self._after(lambda: self.stop_btn.configure(state='disabled'))

    def _wait_for_server(self, timeout=60):
        """等待服务就绪"""
        host, port = self._get_api_host_port()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._stop_event.is_set():
                return False
            try:
                urllib.request.urlopen(f"http://{host}:{port}/health", timeout=2)
                return True
            except Exception:
                try:
                    urllib.request.urlopen(f"http://{host}:{port}/v1/models", timeout=2)
                    return True
                except Exception:
                    pass
            time.sleep(0.5)
        return False

    def _get_api_host_port(self):
        """获取当前 host/port"""
        host = self.config.get_param_value('host', '127.0.0.1')
        port = self.config.get_param_value('port', 8080)
        return host, port

    def _build_benchmark_prompt(self, ctx_size):
        """构造一个大致填满指定 ctx 的 prompt"""
        # 中文字符约 1 token/字，简单估算
        target_chars = max(100, ctx_size // 4)
        words = "人工智能 " * (target_chars // 5)
        return f"请总结以下关键词：{words}。简要说明它们的关系。"

    def _send_benchmark_request(self, host, port, ctx_size):
        """发送流式 API 请求并测量首 token 时间 / 生成速度

        大上下文下 llama-server 的 stdout 可能缓冲，导致日志解析不到速度。
        流式请求可以直接测量：首 token 前为 prompt eval，首 token 后为 generation。
        如果 llama-server 支持 stream_options.include_usage，则使用返回的精确 token 数。
        """
        prompt = self._build_benchmark_prompt(ctx_size)
        try:
            return self._send_benchmark_stream(host, port, prompt, use_usage=True)
        except urllib.error.HTTPError as e:
            if e.code in (400, 422):
                # 服务器不支持 stream_options，重试一次不带 usage
                return self._send_benchmark_stream(host, port, prompt, use_usage=False)
            raise

    def _send_benchmark_stream(self, host, port, prompt, use_usage=True):
        """流式请求核心实现"""
        url = f"http://{host}:{port}/v1/chat/completions"
        payload = {
            "model": "model",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 256,
            "stream": True,
        }
        if use_usage:
            payload["stream_options"] = {"include_usage": True}

        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}, method='POST')

        t0 = time.time()
        first_token_time = None
        t_gen_start = None
        t_gen_end = None
        gen_chars = 0
        usage = {}

        with urllib.request.urlopen(req, timeout=300) as resp:
            for raw in resp:
                line = raw.decode('utf-8', errors='replace').strip()
                if not line.startswith('data:'):
                    continue
                content = line[5:].strip()
                if content == '[DONE]':
                    t_gen_end = time.time()
                    break
                try:
                    obj = json.loads(content)
                except Exception:
                    continue

                # 最终 usage 块（stream_options.include_usage 启用时）
                if use_usage and 'usage' in obj and obj['usage']:
                    usage.update(obj['usage'])
                    continue

                choices = obj.get('choices', [])
                if not choices:
                    continue
                delta = choices[0].get('delta', {})
                text = delta.get('content', '') or ''
                if text:
                    if first_token_time is None:
                        first_token_time = time.time()
                        t_gen_start = first_token_time
                    gen_chars += len(text)

        if t_gen_start is None:
            return None
        if t_gen_end is None:
            t_gen_end = time.time()

        prompt_time = first_token_time - t0
        gen_time = t_gen_end - t_gen_start

        if use_usage and usage.get('prompt_tokens') and usage.get('completion_tokens'):
            prompt_tokens = usage['prompt_tokens']
            gen_tokens = usage['completion_tokens']
        else:
            # 退而使用字符数估算（中文模型约 1 token/字，保守用 1:1）
            prompt_tokens = len(prompt)
            gen_tokens = gen_chars

        prompt_rate = prompt_tokens / prompt_time if prompt_time > 0 else 0
        gen_rate = gen_tokens / gen_time if gen_time > 0 else 0
        return {'prompt_eval_rate': prompt_rate, 'eval_rate': gen_rate}

    def _reset_stats(self):
        """重置统计缓存，确保下一轮只读取当前请求的日志"""
        with self._lock:
            self._last_stats.clear()
            self._stats_event.clear()

    def _wait_for_token_stats(self, timeout=60):
        """等待日志解析出 prompt 和生成速度；接受部分数据"""
        start = time.time()
        while time.time() - start < timeout:
            if self._stop_event.is_set():
                return None
            with self._lock:
                stats = dict(self._last_stats)
            prompt = stats.get('prompt_eval_rate') or stats.get('slot_prompt')
            gen = stats.get('eval_rate') or stats.get('slot_generation')
            if prompt or gen:
                # 已收到至少一个速度，再稍等一下让另一个也到达
                self._stats_event.wait(timeout=1.5)
                with self._lock:
                    stats = dict(self._last_stats)
                return stats
            # 等待新数据到来
            self._stats_event.wait(timeout=0.2)
            self._stats_event.clear()
        # 超时前返回已收到的部分数据
        with self._lock:
            return dict(self._last_stats)

    def _dump_server_logs(self, ctx):
        """未解析到速度时，把最近服务器输出写入日志，方便诊断"""
        try:
            lines = self.server_mgr.get_recent_logs(200)
            self.logger.warning(f"[Benchmark ctx={ctx}] 未解析到速度，最近服务器输出：")
            for line in lines:
                self.logger.warning(f"  | {line}")
        except Exception:
            pass

    def _insert_result(self, ctx, prompt, gen, status):
        """线程安全插入结果"""
        def _do():
            for item in self.tree.get_children():
                if self.tree.item(item, 'values')[0] == str(ctx):
                    self.tree.delete(item)
            values = (
                str(ctx),
                f"{prompt:.2f}" if prompt is not None else "--",
                f"{gen:.2f}" if gen is not None else "--",
                status
            )
            self.tree.insert('', 'end', values=values)
        self._after(_do)

    def _set_progress(self, value):
        """线程安全更新进度"""
        self._after(lambda: self.progress.configure(value=value))

    def _set_status(self, text):
        """线程安全更新状态"""
        self._after(lambda: self.status_var.set(text))

    def _after(self, func):
        """后台线程提交 UI 更新任务，不直接调用 tkinter"""
        UiExecutor().submit(func)


class ApiTab:
    """API 测试 Tab"""

    def __init__(self, parent, config_mgr, logger):
        self.frame = ttk.Frame(parent)
        self.config = config_mgr
        self.logger = logger
        self._build_ui()

    def _build_ui(self):
        ttk.Label(self.frame, text="URL:").pack(anchor='w', padx=4)
        self.url_var = tk.StringVar(value="http://127.0.0.1:8080/v1/chat/completions")
        ttk.Entry(self.frame, textvariable=self.url_var).pack(fill=tk.X, padx=4, pady=2)

        ttk.Label(self.frame, text="Prompt:").pack(anchor='w', padx=4)
        self.prompt_text = tk.Text(self.frame, height=4)
        self.prompt_text.insert('1.0', "你好，请简单介绍一下自己。")
        self.prompt_text.pack(fill=tk.X, padx=4, pady=2)

        ttk.Button(self.frame, text="发送", command=self._send).pack(anchor='w', padx=4, pady=2)

        ttk.Label(self.frame, text="响应:").pack(anchor='w', padx=4)
        self.response_text = tk.Text(self.frame, height=12, wrap=tk.WORD)
        self.response_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)

    def _send(self):
        """发送 API 请求"""
        url = self.url_var.get().strip()
        prompt = self.prompt_text.get('1.0', tk.END).strip()
        if not url or not prompt:
            return
        payload = {
            "model": "model",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 256,
            "temperature": 0.7,
            "stream": False
        }
        try:
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode('utf-8')
            self.response_text.delete('1.0', tk.END)
            self.response_text.insert('1.0', body)
        except Exception as e:
            self.response_text.delete('1.0', tk.END)
            self.response_text.insert('1.0', f"请求失败: {e}")


class LogTab:
    """启动日志 Tab"""

    MAX_LINES = 1000

    def __init__(self, parent, server_mgr, logger):
        self.frame = ttk.Frame(parent)
        self.server_mgr = server_mgr
        self.logger = logger
        self._buffer = []
        self._buffer_lock = threading.Lock()
        self._flush_pending = False
        self._build_ui()
        self.server_mgr.set_callback('on_output', self._on_output)

    def _build_ui(self):
        self.text = scrolledtext.ScrolledText(self.frame, wrap=tk.WORD, font=("Consolas", 9))
        self.text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        ttk.Button(self.frame, text="清空", command=self._clear).pack(anchor='w', padx=4, pady=2)

    def _on_output(self, line):
        """后台线程回调，仅把日志行放入缓冲，不碰 tkinter"""
        with self._buffer_lock:
            self._buffer.append(line)
            if not self._flush_pending:
                self._flush_pending = True
                UiExecutor().submit(self._flush)

    def _flush(self):
        """在主线程中批量刷新日志到 UI；按 chunk 处理，避免单次阻塞"""
        with self._buffer_lock:
            lines = self._buffer
            self._buffer = []
            self._flush_pending = False
        if not self.frame.winfo_exists() or not lines:
            return
        # 每次最多处理 100 行，剩余部分立即重新提交，防止 UI 单次阻塞
        CHUNK = 100
        if len(lines) > CHUNK:
            chunk = lines[:CHUNK]
            rest = lines[CHUNK:]
            with self._buffer_lock:
                self._buffer = rest + self._buffer
                if self._buffer and not self._flush_pending:
                    self._flush_pending = True
                    UiExecutor().submit(self._flush)
        else:
            chunk = lines
        self.text.insert(tk.END, '\n'.join(chunk) + '\n')
        self.text.see(tk.END)
        # 限制最大行数，防止内存和渲染越来越慢
        try:
            total = int(self.text.index('end-1c').split('.')[0])
            if total > self.MAX_LINES:
                self.text.delete('1.0', f'{total - self.MAX_LINES}.0')
        except Exception:
            pass

    def _clear(self):
        self.text.delete('1.0', tk.END)


class FilePathsTab:
    """文件路径 Tab"""

    def __init__(self, parent, config_mgr, logger):
        self.frame = ttk.Frame(parent)
        self.config = config_mgr
        self.logger = logger
        self.file_vars = {}
        self._build_ui()

    def _build_ui(self):
        file_frame = ttk.Frame(self.frame)
        file_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        file_configs = [
            ("log_file", "日志文件", "file", [("Log", "*.log"), ("All files", "*.*")],
             "启动日志保存位置。如填写，llama-server 会把运行日志写入该文件。"),
            ("lora_file", "LoRA适配器", "file", [("GGUF/Adapter", "*.gguf;*.bin"), ("All files", "*.*")],
             "加载一个 LoRA 适配器文件。需要基础模型支持该 LoRA。"),
            ("control_vector_file", "控制向量", "file", [("All files", "*.*")],
             "加载控制向量文件，用于微调模型风格/情绪。需要对应模型。"),
            ("grammar_file", "语法文件", "file", [("GBNF", "*.gbnf"), ("All files", "*.*")],
             "GBNF 语法约束文件。可限制输出格式，例如强制 JSON。"),
            ("json_schema_file", "JSON Schema", "file", [("JSON", "*.json"), ("All files", "*.*")],
             "JSON Schema 文件。开启结构输出时使用。"),
            ("webui_path", "网页UI目录", "dir", [],
             "llama-server 使用的网页 UI 静态文件目录。留空则使用内置 UI。"),
        ]

        for key, label, kind, ftypes, help_text in file_configs:
            row = ttk.Frame(file_frame)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=f"{label}:", width=12, anchor='e').pack(side=tk.LEFT)
            info_lbl = ttk.Label(row, text='ⓘ', foreground='#adb5bd', width=2, anchor='center')
            info_lbl.pack(side=tk.LEFT)
            ToolTip(info_lbl, text=help_text, bootstyle="light", wraplength=280, delay=200)
            var = tk.StringVar(value=self.config.get(f"paths.{key}", ""))
            self.file_vars[key] = var
            entry = ttk.Entry(row, textvariable=var)
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
            ttk.Button(row, text="浏览", width=5,
                       command=lambda k=key, v=var, f=ftypes, knd=kind: self._browse_file(k, v, f, knd)).pack(side=tk.LEFT)

    def _browse_file(self, key, var, filetypes, kind="file"):
        """浏览文件或目录"""
        if kind == "dir":
            path = browse_directory(f"选择 {key}")
        else:
            path = browse_file(f"选择 {key}", filetypes)
        if path:
            var.set(path)
            self.config.set(f"paths.{key}", path)
            self.config.save()


class RightPanel:
    """右侧Tab面板容器"""

    def __init__(self, parent, config_mgr, server_mgr, logger, param_mgr=None):
        self.parent = parent
        self.config = config_mgr
        self.server_mgr = server_mgr
        self.logger = logger
        self.param_mgr = param_mgr

        self.notebook = ttk.Notebook(parent)

        # 创建各Tab
        self.start_tab = StartConfigTab(self.notebook, config_mgr, server_mgr, logger)
        self.bench_tab = BenchmarkTab(self.notebook, config_mgr, server_mgr, logger, param_mgr)
        self.api_tab = ApiTab(self.notebook, config_mgr, logger)
        self.log_tab = LogTab(self.notebook, server_mgr, logger)
        self.file_paths_tab = FilePathsTab(self.notebook, config_mgr, logger)

        self.notebook.add(self.start_tab.frame, text="启动配置")
        self.notebook.add(self.bench_tab.frame, text="批量测速")
        self.notebook.add(self.api_tab.frame, text="API测试")
        self.notebook.add(self.log_tab.frame, text="启动日志")
        self.notebook.add(self.file_paths_tab.frame, text="文件路径")

        # 运行状态监控
        host = self.config.get_param_value('host', '127.0.0.1')
        port = self.config.get_param_value('port', 8080)
        self.status_monitor = StatusMonitor(server_mgr, host, port, interval=1.0)
        self.status_tab = StatusTab(self.notebook, server_mgr, self.status_monitor, logger)
        self.notebook.add(self.status_tab.frame, text="运行状态")
        self.status_monitor.start()

        # 恢复上次选中的tab
        last_tab = self.config.get("ui.last_tab", 0)
        if isinstance(last_tab, int) and 0 <= last_tab < 6:
            self.notebook.select(last_tab)

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _on_tab_changed(self, event):
        idx = self.notebook.index(self.notebook.select())
        self.config.set("ui.last_tab", idx)
        self.config.save()

    def update_selected_model(self, model_path):
        """更新选中的模型显示"""
        self.start_tab.update_selected_model(model_path)

    def refresh(self):
        """刷新启动配置Tab"""
        self.start_tab.refresh()
        # 同步 host/port 到状态监控
        try:
            host = self.config.get_param_value('host', '127.0.0.1')
            port = self.config.get_param_value('port', 8080)
            self.status_monitor.set_host_port(host, port)
        except Exception:
            pass

    def set_param_change_callback(self, callback):
        """设置参数变化回调，委托给启动配置Tab"""
        self.start_tab.set_param_change_callback(callback)

    def stop(self):
        """停止运行状态监控与状态 Tab 回调"""
        try:
            if hasattr(self, 'status_tab') and self.status_tab:
                self.status_tab.stop()
        except Exception as e:
            self.logger.error(f"停止状态 Tab 失败: {e}")
        try:
            if hasattr(self, 'status_monitor') and self.status_monitor:
                self.status_monitor.stop()
        except Exception as e:
            self.logger.error(f"停止状态监控失败: {e}")

    def get_widget(self):
        return self.notebook
