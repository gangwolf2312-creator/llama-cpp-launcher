"""
左侧面板
包含: 参数调节面板(分类折叠)、系统提示词区域
"""
import tkinter as tk
from tkinter import ttk, scrolledtext
import ttkbootstrap as ttkb
from core.param_manager import ParamControlFactory


class LeftPanel:
    """左侧面板 - 仅保留参数设置"""

    def __init__(self, parent, config_mgr, param_mgr, logger, on_param_change=None):
        self.parent = parent
        self.config = config_mgr
        self.param_mgr = param_mgr
        self.logger = logger
        self.on_param_change = on_param_change
        self._refreshing = False
        self._flush_timer = None
        self._last_change = None

        self.frame = ttk.Frame(parent)
        self._build_ui()

    def _build_ui(self):
        """构建UI"""
        # 创建可滚动Canvas
        self.canvas = tk.Canvas(self.frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 鼠标滚轮
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind('<Configure>', self._on_canvas_configure)

        # 参数调节面板
        self._build_param_panel()

        # 系统提示词区域
        self._build_prompt_section()

    def _on_canvas_configure(self, event):
        """Canvas大小变化时调整内部frame宽度"""
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        """鼠标滚轮滚动"""
        self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")

    def _build_param_panel(self):
        """参数调节面板 - 分类折叠"""
        param_frame = ttk.LabelFrame(self.scrollable_frame, text="\u53c2\u6570\u8c03\u8282", padding=5)
        param_frame.pack(fill=tk.X, padx=4, pady=4)

        # 温度预设下拉
        preset_frame = ttk.Frame(param_frame)
        preset_frame.pack(fill=tk.X, pady=2)
        ttk.Label(preset_frame, text="\u6e29\u5ea6\u9884\u8bbe:").pack(side=tk.LEFT)
        presets = list(self.config.get("temperature_presets", {}).keys())
        self.preset_var = tk.StringVar(value="\u9009\u62e9\u9884\u8bbe...")
        preset_combo = ttk.Combobox(preset_frame, textvariable=self.preset_var,
                                     values=presets, state="readonly", width=20)
        preset_combo.pack(side=tk.LEFT, padx=4)
        preset_combo.bind("<<ComboboxSelected>>", self._apply_temp_preset)

        # 分类折叠面板
        self.param_controls = {}  # name -> widgets
        self.category_frames = {}  # cat_id -> (toggle_btn, content_frame)

        categories = self.param_mgr.get_categories()
        for cat in categories:
            cat_id = cat['id']
            cat_name = f"{cat.get('icon', '')} {cat['name']} ({len(cat['params'])}\u4e2a)"

            # 折叠按钮
            toggle_btn = ttk.Button(param_frame, text=f"\u25bc {cat_name}",
                                   command=lambda c=cat_id: self._toggle_category(c))
            toggle_btn.pack(fill=tk.X, pady=1)

            # 内容框架
            content = ttk.Frame(param_frame)
            content.pack(fill=tk.X)

            self.category_frames[cat_id] = (toggle_btn, content)

            # 为该分类的每个参数创建控件
            for param_def in cat['params']:
                if param_def.get('hidden'):
                    continue
                frame, widgets = ParamControlFactory.create_control(
                    content, param_def, self.config,
                    on_change=self._on_param_changed
                )
                frame.pack(fill=tk.X, pady=1, padx=4)
                self.param_controls[param_def['name']] = widgets

        # 初始化冲突状态
        self._rebuild_conflicts()

    def _build_prompt_section(self):
        """系统提示词区域"""
        prompt_frame = ttk.LabelFrame(self.scrollable_frame, text="\u7cfb\u7edf\u63d0\u793a\u8bcd", padding=5)
        prompt_frame.pack(fill=tk.X, padx=4, pady=4)

        # 预设选择
        preset_frame = ttk.Frame(prompt_frame)
        preset_frame.pack(fill=tk.X, pady=2)
        ttk.Label(preset_frame, text="\u9884\u8bbe:").pack(side=tk.LEFT)
        prompts = self.config.get("system_prompts", {})
        self.prompt_preset_var = tk.StringVar(value=self.config.get("server.last_preset", "\ud83e\udd16 AI\u52a9\u624b"))
        preset_combo = ttk.Combobox(preset_frame, textvariable=self.prompt_preset_var,
                                     values=list(prompts.keys()), state="readonly", width=20)
        preset_combo.pack(side=tk.LEFT, padx=4)
        preset_combo.bind("<<ComboboxSelected>>", self._on_prompt_preset_changed)

        ttk.Button(preset_frame, text="\u4fdd\u5b58", command=self._save_prompt).pack(side=tk.LEFT, padx=2)
        ttk.Button(preset_frame, text="\u91cd\u7f6e", command=self._reset_prompt).pack(side=tk.LEFT, padx=2)

        # 编辑区
        self.prompt_text = scrolledtext.ScrolledText(prompt_frame, height=6, wrap=tk.WORD)
        self.prompt_text.pack(fill=tk.X, pady=2)
        self.prompt_text.bind('<FocusOut>', self._on_prompt_text_changed)

        # 初始填充
        current_preset = self.prompt_preset_var.get()
        if current_preset in prompts:
            self.prompt_text.delete('1.0', tk.END)
            self.prompt_text.insert('1.0', prompts[current_preset])
        self._sync_system_prompt()

    def _toggle_category(self, cat_id):
        """切换分类折叠状态"""
        toggle_btn, content = self.category_frames[cat_id]
        if content.winfo_viewable():
            content.pack_forget()
            toggle_btn.configure(text=toggle_btn.cget('text').replace('\u25bc', '\u25b6'))
        else:
            content.pack(fill=tk.X)
            toggle_btn.configure(text=toggle_btn.cget('text').replace('\u25b6', '\u25bc'))

    def _on_param_changed(self, name, enabled, value):
        """参数变更回调：只更新内存，批量刷新推迟到防抖后"""
        if self._refreshing:
            return
        self.config.set_param_enabled(name, enabled)
        if value is not None:
            self.config.set_param_value(name, value)
        self._last_change = (name, enabled, value)
        self._schedule_flush()

    def _schedule_flush(self):
        """延迟刷新，避免每次滑块/按键都写盘和重建冲突"""
        if self._flush_timer:
            self.frame.after_cancel(self._flush_timer)
        self._flush_timer = self.frame.after(300, self._flush_param_changes)

    def _flush_param_changes(self):
        """延迟后统一落盘、通知右侧面板、重建冲突"""
        self._flush_timer = None
        self.config.save("system")
        if self.on_param_change and self._last_change:
            self.on_param_change(*self._last_change)
            self._last_change = None
        self._rebuild_conflicts()

    def _rebuild_conflicts(self):
        """根据当前已启用参数重新计算并锁定相互排斥的参数"""
        # 收集当前已启用的参数
        enabled = set()
        for p in self.param_mgr.get_all_params():
            if self.config.is_param_enabled(p['name']):
                enabled.add(p['name'])

        # 计算哪些参数应该被禁用
        disabled_by_conflict = set()
        for other in enabled:
            param_def = self.param_mgr.get_param_def(other)
            if not param_def:
                continue
            for target in param_def.get('conflicts', []):
                disabled_by_conflict.add(target)

        # 更新 UI 状态
        for p in self.param_mgr.get_all_params():
            name = p['name']
            widgets = self.param_controls.get(name)
            if not widgets:
                continue
            should_lock = name in disabled_by_conflict
            if should_lock:
                # 冲突项：取消勾选并锁定复选框
                if self.config.is_param_enabled(name):
                    widgets['enable_var'].set(False)
                    self.config.set_param_enabled(name, False)
                    if self.on_param_change and not self._refreshing:
                        self.on_param_change(name, False, None)
                widgets['enable_cb'].configure(state='disabled')
                ParamControlFactory._update_state(widgets, False)
            else:
                # 非冲突项：恢复复选框可用，值控件状态跟随启用状态
                widgets['enable_cb'].configure(state='normal')
                ParamControlFactory._update_state(widgets, self.config.is_param_enabled(name))

    def _apply_temp_preset(self, event=None):
        """应用温度/采样预设，同时处理参数冲突"""
        preset_name = self.preset_var.get()
        presets = self.config.get("temperature_presets", {})
        if preset_name not in presets:
            return
        p = presets[preset_name]

        # 将 Kimi 等通用命名映射到本程序的参数名
        key_map = {
            'temperature': 'temp',
            'top_k': 'top-k',
            'top_p': 'top-p',
            'top-k': 'top-k',
            'top-p': 'top-p',
            'min_p': 'min-p',
            'min-p': 'min-p',
            'repeat_penalty': 'repeat-penalty',
            'repeat-penalty': 'repeat-penalty',
            'repeat_last_n': 'repeat-last-n',
            'repeat-last-n': 'repeat-last-n',
            'mirostat_mode': 'mirostat',
            'mirostat_tau': 'mirostat-ent',
            'mirostat_eta': 'mirostat-lr',
            'mirostat-mode': 'mirostat',
            'mirostat-tau': 'mirostat-ent',
            'mirostat-eta': 'mirostat-lr',
            'frequency_penalty': 'frequency-penalty',
            'frequency-penalty': 'frequency-penalty',
            'presence_penalty': 'presence-penalty',
            'presence-penalty': 'presence-penalty',
            'dynatemp_range': 'dynatemp-range',
            'dynatemp_exp': 'dynatemp-exp',
            'dynatemp-range': 'dynatemp-range',
            'dynatemp-exp': 'dynatemp-exp',
            'typical_p': 'typical',
            'typical-p': 'typical',
            'typical': 'typical',
        }

        applied = []
        for raw_key, value in p.items():
            if raw_key in ('name', 'description'):
                continue
            key = key_map.get(raw_key, raw_key)
            # mirostat=0 表示关闭，启用它会触发冲突逻辑禁用 temp/top-k 等采样参数
            if key == 'mirostat' and value == 0:
                continue
            widgets = self.param_controls.get(key)
            if not widgets:
                continue
            widgets['enable_var'].set(True)
            widgets['var'].set(value)
            self.config.set_param_enabled(key, True)
            self.config.set_param_value(key, value)
            applied.append(key)

        self._rebuild_conflicts()
        self.config.save("system")
        if applied:
            self.logger.info(f"\u5e94\u7528\u6e29\u5ea6\u9884\u8bbe: {preset_name} ({', '.join(applied)})")

    def _on_prompt_preset_changed(self, event=None):
        """提示词预设切换"""
        preset = self.prompt_preset_var.get()
        prompts = self.config.get("system_prompts", {})
        if preset in prompts:
            self.prompt_text.delete('1.0', tk.END)
            self.prompt_text.insert('1.0', prompts[preset])
            self.config.set("server.last_preset", preset)
            self.config.save()
            self._sync_system_prompt()

    def _save_prompt(self):
        """保存当前提示词"""
        preset = self.prompt_preset_var.get()
        prompts = self.config.get("system_prompts", {})
        if preset in prompts:
            prompts[preset] = self.prompt_text.get('1.0', tk.END).strip()
            self.config.set("system_prompts", prompts)
            self.config.save()
            self.logger.info(f"\u4fdd\u5b58\u63d0\u793a\u8bcd: {preset}")
        self._sync_system_prompt()

    def _reset_prompt(self):
        """重置提示词为默认值"""
        defaults = self.config.get("system_prompts_defaults", {})
        preset = self.prompt_preset_var.get()
        if preset in defaults:
            self.prompt_text.delete('1.0', tk.END)
            self.prompt_text.insert('1.0', defaults[preset])
            self._sync_system_prompt()

    def _sync_system_prompt(self):
        """同步系统提示词到隐藏参数"""
        text = self.prompt_text.get('1.0', tk.END).strip()
        self.config.set_param_value('system-prompt', text)
        self.config.set_param_enabled('system-prompt', bool(text))
        self.config.save("system")
        if self.on_param_change:
            self.on_param_change('system-prompt', bool(text), text)

    def _on_prompt_text_changed(self, event=None):
        """提示词编辑失焦后同步到启动参数"""
        self._sync_system_prompt()

    def refresh_params(self):
        """根据当前配置重新刷新所有参数控件（用于右侧面板应用硬件推荐后同步）"""
        import os
        self._refreshing = True
        if self._flush_timer:
            self.frame.after_cancel(self._flush_timer)
            self._flush_timer = None
            self._last_change = None
        try:
            for name, widgets in self.param_controls.items():
                param_def = self.param_mgr.get_param_def(name)
                if not param_def:
                    continue

                enabled = self.config.is_param_enabled(name)
                widgets['enable_var'].set(enabled)

                ptype = widgets.get('type')
                if ptype != 'bool':
                    value = self.config.get_param_value(name)
                    if value is not None and 'var' in widgets:
                        try:
                            widgets['var'].set(value)
                        except Exception as e:
                            self.logger.warning(f"刷新参数 {name} 值失败: {e}")

                # 更新关联文件浏览的路径标签
                browse_cfg = param_def.get('browse')
                path_lbl = widgets.get('path_lbl')
                if browse_cfg and path_lbl:
                    key = browse_cfg['key']
                    current_path = self.config.get(f"paths.{key}")
                    display = os.path.basename(current_path) if current_path else "未选择"
                    path_lbl.configure(text=display)
                    if current_path:
                        ParamControlFactory._add_tooltip(path_lbl, current_path)

            self._rebuild_conflicts()
        finally:
            self._refreshing = False

    def get_frame(self):
        return self.frame
