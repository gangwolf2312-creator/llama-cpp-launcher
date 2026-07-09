"""
参数管理系统
- SystemParamManager: 参数定义加载与命令行构建
- ParamControlFactory: 根据参数类型动态创建UI控件
"""
import json
import os
import shlex
import threading
from pathlib import Path


class SystemParamManager:
    """系统参数管理器 - 单例"""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, params_file=None):
        if self._initialized:
            return
        self._initialized = True

        if params_file is None:
            params_file = Path(os.path.dirname(os.path.abspath(__file__))).parent / "llama-params.json"
        self.params_file = Path(params_file)
        self._categories = []
        self._param_map = {}  # name -> param_def
        self._load_params()

    def _load_params(self):
        """加载参数定义"""
        try:
            with open(self.params_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._categories = data.get('categories', [])
            self._param_map = {}
            for cat in self._categories:
                for p in cat.get('params', []):
                    self._param_map[p['name']] = p
        except Exception as e:
            print(f"[ParamManager] Load error: {e}")
            self._categories = []
            self._param_map = {}

    def get_categories(self):
        """获取所有分类"""
        return self._categories

    def get_param_def(self, name):
        """获取参数定义"""
        return self._param_map.get(name)

    def get_all_params(self):
        """获取所有参数定义列表"""
        return list(self._param_map.values())

    def build_command_line(self, config_mgr):
        """根据启用的参数构建命令行参数列表"""
        from core.config_manager import ConfigManager
        if isinstance(config_mgr, ConfigManager):
            cfg = config_mgr
        else:
            cfg = ConfigManager()

        args = []
        for name, param_def in self._param_map.items():
            if not cfg.is_param_enabled(name):
                continue

            # 取值, 没有则用参数定义中的默认值
            value = cfg.get_param_value(name)
            if value is None:
                value = param_def.get('default')
            if value is None:
                continue

            # 额外参数：原样拼入命令行
            if name == 'extra-args':
                raw = str(value).strip()
                if raw:
                    args.extend(shlex.split(raw))
                continue

            arg_name = param_def.get('arg', f'--{name}')
            ptype = param_def.get('type', 'string')

            # 枚举型：值必须在选项列表中，否则回退默认值
            if ptype == 'enum':
                options = param_def.get('options', [])
                if value not in options and options:
                    value = param_def.get('default')
                if value not in options:
                    continue

            # 布尔型: 只要勾选就加参数名
            if ptype == 'bool':
                args.append(arg_name)
                # 如果有关联文件路径,且文件存在,则一并追加
                browse_cfg = param_def.get('browse')
                if browse_cfg:
                    key = browse_cfg.get('key')
                    file_arg = browse_cfg.get('arg', f'--{key}')
                    path = cfg.get(f'paths.{key}', '')
                    if path and str(path).strip():
                        args.append(file_arg)
                        args.append(str(path).strip())
            # 文件型/文本型/字符串/枚举: 空值跳过
            elif ptype in ('file', 'text', 'string', 'enum'):
                if str(value).strip():
                    args.append(arg_name)
                    args.append(str(value))
            # 数值型
            else:
                args.append(arg_name)
                args.append(str(value))

        return args


class ParamControlFactory:
    """参数控件工厂 - 根据参数类型创建对应的tkinter控件"""

    @staticmethod
    def create_control(parent, param_def, config_mgr, on_change=None):
        """
        创建参数对应的UI控件
        返回: (frame, widgets_dict)
        widgets_dict 包含 'var', 'widget', 'enable_var', 'enable_cb' 等
        """
        import tkinter as tk
        from tkinter import ttk
        import ttkbootstrap as ttkb

        ptype = param_def.get('type', 'string')
        name = param_def['name']
        label_text = param_def.get('label', name)
        default = param_def.get('default', '')
        arg = param_def.get('arg', f'--{name}')
        help_text = param_def.get('help', '')

        frame = ttk.Frame(parent)

        # 启用复选框 + 标签
        enable_var = tk.BooleanVar(value=config_mgr.is_param_enabled(name))
        enable_cb = ttk.Checkbutton(frame, variable=enable_var, width=2)
        enable_cb.pack(side=tk.LEFT, padx=(2, 0))

        lbl = ttk.Label(frame, text=label_text, width=30, anchor='w')
        lbl.pack(side=tk.LEFT, padx=(2, 4))

        # 提示图标
        if help_text:
            info_lbl = ttk.Label(frame, text='ⓘ', foreground='#adb5bd', width=3, anchor='center', font=('Arial', 10))
            info_lbl.pack(side=tk.LEFT, padx=(0, 6))
            ParamControlFactory._add_tooltip(info_lbl, help_text)
            ParamControlFactory._add_tooltip(lbl, help_text)

        # 可选关联文件浏览按钮 + 路径显示
        browse_btn = None
        path_lbl = None
        browse_cfg = param_def.get('browse')
        if browse_cfg:
            key = browse_cfg['key']
            current_path = config_mgr.get(f"paths.{key}")
            display = os.path.basename(current_path) if current_path else "\u672a\u9009\u62e9"
            path_lbl = ttk.Label(frame, text=display, foreground='#adb5bd', anchor='w', font=('Arial', 8))
            path_lbl.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
            if current_path:
                ParamControlFactory._add_tooltip(path_lbl, current_path)
            browse_btn = ttk.Button(
                frame, text='...', width=3,
                command=lambda p=param_def, b=browse_cfg, cfg=config_mgr, cb=on_change, pl=path_lbl:
                    ParamControlFactory._browse_for(p, b, cfg, pl, cb)
            )
            browse_btn.pack(side=tk.LEFT, padx=2)

        widgets = {
            'frame': frame,
            'enable_var': enable_var,
            'enable_cb': enable_cb,
            'type': ptype,
            'name': name,
            'arg': arg,
            'label': lbl,
            'browse_btn': browse_btn,
            'path_lbl': path_lbl
        }

        # 根据类型创建控件
        if ptype == 'bool':
            # 布尔型只有一个复选框：勾选即启用，同时值也为 True
            widgets['var'] = enable_var

        elif ptype == 'int':
            stored = config_mgr.get_param_value(name, default)
            values = param_def.get('values')
            try:
                init_val = int(stored)
            except (TypeError, ValueError):
                init_val = None
            if values:
                if init_val not in values:
                    try:
                        init_val = int(default) if default in values else int(values[0])
                    except (TypeError, ValueError):
                        init_val = 0
                    # 旧配置残留非法值，直接对齐回档位
                    config_mgr.set_param_value(name, init_val)
            else:
                if init_val is None:
                    try:
                        init_val = int(default)
                    except (TypeError, ValueError):
                        init_val = 0
            var = tk.IntVar(value=init_val)
            if values:
                spin = ttk.Spinbox(frame, values=[str(v) for v in values], textvariable=var, width=12)
            else:
                min_val = param_def.get('min', 0)
                max_val = param_def.get('max', 999999)
                step = param_def.get('step', 1)
                spin = ttk.Spinbox(frame, from_=min_val, to=max_val, increment=step, textvariable=var, width=12)
            spin.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
            widgets['var'] = var
            widgets['widget'] = spin

        elif ptype == 'float':
            stored = config_mgr.get_param_value(name, default)
            try:
                init_val = float(stored)
            except (TypeError, ValueError):
                try:
                    init_val = float(default)
                except (TypeError, ValueError):
                    init_val = 0.0
            var = tk.DoubleVar(value=init_val)
            entry = ttk.Entry(frame, textvariable=var, width=12)
            entry.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
            # 滑块
            step = param_def.get('step', 0.01)
            min_val = param_def.get('min', 0.0)
            max_val = param_def.get('max', 1.0)
            scale = ttk.Scale(frame, from_=min_val, to=max_val, variable=var,
                            orient=tk.HORIZONTAL, length=100)
            scale.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
            widgets['var'] = var
            widgets['widget'] = entry
            widgets['scale'] = scale

        elif ptype == 'enum':
            options = param_def.get('options', [])
            stored = config_mgr.get_param_value(name, default)
            # 兼容旧配置：如果保存的值不在当前选项中，回退到默认值
            if stored not in options and options:
                stored = default if default in options else options[0]
            var = tk.StringVar(value=stored)
            combo = ttk.Combobox(frame, textvariable=var, values=options, state='readonly', width=14)
            combo.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
            widgets['var'] = var
            widgets['widget'] = combo

        elif ptype == 'file':
            var = tk.StringVar(value=config_mgr.get_param_value(name, default))
            entry = ttk.Entry(frame, textvariable=var, width=20)
            entry.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
            btn = ttk.Button(frame, text="\u6d4f\u89c8", width=5,
                           command=lambda v=var: ParamControlFactory._browse_file(v))
            btn.pack(side=tk.LEFT, padx=2)
            widgets['var'] = var
            widgets['widget'] = entry
            widgets['browse_btn'] = btn

        elif ptype == 'text':
            # text类型用Entry简化
            var = tk.StringVar(value=config_mgr.get_param_value(name, default))
            entry = ttk.Entry(frame, textvariable=var, width=30)
            entry.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
            widgets['var'] = var
            widgets['widget'] = entry

        elif ptype == 'string':
            var = tk.StringVar(value=config_mgr.get_param_value(name, default))
            entry = ttk.Entry(frame, textvariable=var, width=25)
            entry.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
            widgets['var'] = var
            widgets['widget'] = entry

        # 绑定变更回调
        def _safe_get(var, default=None):
            """变量在过渡态（如 Entry 被清空、控件销毁时）get() 会抛 TclError，这里兜底"""
            try:
                return var.get()
            except tk.TclError:
                return default

        def _safe_bool(var):
            try:
                return bool(var.get())
            except tk.TclError:
                return False

        if on_change:
            if ptype == 'bool':
                enable_var.trace_add('write', lambda *a, n=name, e=enable_var, sg=_safe_bool: on_change(n, sg(e), sg(e)))
            else:
                enable_var.trace_add('write', lambda *a, n=name, e=enable_var, sg=_safe_bool: on_change(n, sg(e), None))
                if 'var' in widgets:
                    widgets['var'].trace_add('write', lambda *a, n=name, e=enable_var, v=widgets['var'], sg=_safe_get: on_change(n, sg(e), sg(v)))

        # 初始状态
        ParamControlFactory._update_state(widgets, _safe_bool(enable_var))
        enable_var.trace_add('write', lambda *a, w=widgets: ParamControlFactory._update_state(w, _safe_bool(w['enable_var'])))

        return frame, widgets

    @staticmethod
    def _update_state(widgets, enabled):
        """更新控件启用/禁用状态"""
        state = 'normal' if enabled else 'disabled'
        if 'widget' in widgets:
            try:
                widgets['widget'].configure(state=state)
            except Exception:
                pass
        if 'scale' in widgets:
            try:
                widgets['scale'].configure(state=state)
            except Exception:
                pass
        if 'browse_btn' in widgets:
            try:
                widgets['browse_btn'].configure(state=state)
            except Exception:
                pass

    @staticmethod
    def _browse_file(var):
        """文件浏览对话框"""
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            filetypes=[("All files", "*.*"), ("GGUF", "*.gguf"), ("JSON", "*.json"), ("Text", "*.txt")]
        )
        if path:
            var.set(path)

    @staticmethod
    def _browse_for(param_def, browse_cfg, config_mgr, path_lbl=None, on_change=None):
        """关联文件浏览：把选中的路径写到 config.paths.<key>，并通知刷新"""
        from tkinter import filedialog
        key = browse_cfg['key']
        filetypes = [tuple(t) for t in browse_cfg.get('filetypes', [["All files", "*.*"]])]
        label = browse_cfg.get('label', key)

        current = config_mgr.get(f"paths.{key}") or config_mgr.get("paths.model_library") or os.path.expanduser("~")
        if current and os.path.isfile(current):
            initialdir = os.path.dirname(current)
        else:
            initialdir = current or os.path.expanduser("~")
        if not initialdir or not os.path.isdir(initialdir):
            initialdir = os.path.expanduser("~")

        path = filedialog.askopenfilename(
            title=f"选择 {label}",
            filetypes=filetypes,
            initialdir=initialdir
        )
        if path:
            config_mgr.set(f"paths.{key}", path)
            config_mgr.save()
            if path_lbl:
                path_lbl.configure(text=os.path.basename(path))
                ParamControlFactory._add_tooltip(path_lbl, path)
            if on_change:
                on_change(param_def['name'], config_mgr.is_param_enabled(param_def['name']), None)

    @staticmethod
    def _add_tooltip(widget, text):
        """添加悬停提示(ttkbootstrap ToolTip)"""
        from ttkbootstrap.widgets import ToolTip
        widget._tooltip = ToolTip(widget, text=text, bootstyle="light", wraplength=300, delay=200)


def get_param_mgr(params_file=None):
    """获取 SystemParamManager 单例"""
    return SystemParamManager(params_file)
