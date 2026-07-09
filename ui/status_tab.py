"""运行状态 Tab
分离的预填充（Prefill）和输出（Output）速度图，基于时间轴。
每个日志采样保存为 (t, value)；采样点之间用折线连接。
阶段结束后没有新采样，曲线和时间轴都停住。
"""
import tkinter as tk
from tkinter import ttk
import time
from core.ui_executor import UiExecutor


class StatusTab:
    """运行状态监控 Tab"""

    # 显示最近 60 秒，最多保留 300 秒
    HISTORY_WINDOW = 60
    HISTORY_MAX = 300

    def __init__(self, parent, server_mgr, status_monitor, logger):
        self.frame = ttk.Frame(parent)
        self.server_mgr = server_mgr
        self.status_monitor = status_monitor
        self.logger = logger

        # 每个指标保存为采样点：t=采样时间，value=速度
        self._points = {'prefill': [], 'output': []}
        # 视图右边缘锚定到最新数据点，不随时间自动移动
        self._view_end = {'prefill': time.time(), 'output': time.time()}
        # 记录上次用 /slots 实时速度打点的时间，避免 1 秒内重复打点
        self._last_status_point = {'prefill': 0.0, 'output': 0.0}
        # 避免短时间多次提交重绘任务
        self._draw_pending = False

        self._build_ui()
        self.status_monitor.add_callback(self._on_status)
        self.server_mgr.set_callback('on_token_stats', self._on_token_stats)
        self._schedule_draw()

    def _build_ui(self):
        """构建 UI"""
        # 顶部状态区
        status_frame = ttk.LabelFrame(self.frame, text="服务状态", padding=8)
        status_frame.pack(fill=tk.X, padx=4, pady=4)

        self.status_vars = {}
        items = [
            ('service_state', '进程状态', '已停止'),
            ('alive', '端口响应', '否'),
            ('uptime', '运行时间', '00:00:00'),
            ('slots', '槽位数', '0 / 0'),
            ('processing', '处理中', '0'),
            ('error', '最近错误', '-'),
        ]
        for i, (key, label, default) in enumerate(items):
            ttk.Label(status_frame, text=f'{label}:', width=10, anchor='e').grid(row=0, column=i * 2, sticky='e', padx=2)
            var = tk.StringVar(value=default)
            self.status_vars[key] = var
            ttk.Label(status_frame, textvariable=var, width=12 if key != 'error' else 30, anchor='w').grid(row=0, column=i * 2 + 1, sticky='w', padx=2)

        # 指示灯（右下角）
        self.indicator = tk.Canvas(status_frame, width=14, height=14, highlightthickness=0)
        self.indicator.grid(row=0, column=12, padx=4)
        self._indicator_id = self.indicator.create_oval(2, 2, 12, 12, fill='gray')

        # 主区域：上下布局（槽位表格 | 预填充图 | 输出速度图）
        main_paned = ttk.PanedWindow(self.frame, orient=tk.VERTICAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # 槽位表格
        table_frame = ttk.LabelFrame(main_paned, text="槽位详情", padding=4)
        table_frame.pack_propagate(False)
        main_paned.add(table_frame, weight=3)

        columns = ('id', 'state', 'ctx', 'prompt', 'gen', 'p_speed', 'g_speed')
        self.slot_tree = ttk.Treeview(
            table_frame, columns=columns, show='headings', height=8
        )
        headings = {
            'id': '槽位ID',
            'state': '状态',
            'ctx': '上下文',
            'prompt': 'Prompt tokens',
            'gen': 'Gen tokens',
            'p_speed': 'Prompt 速度',
            'g_speed': 'Gen 速度',
        }
        widths = {'id': 60, 'state': 80, 'ctx': 80, 'prompt': 100, 'gen': 100, 'p_speed': 100, 'g_speed': 100}
        for col in columns:
            self.slot_tree.heading(col, text=headings[col])
            self.slot_tree.column(col, width=widths[col], anchor='center')
        self.slot_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.slot_tree.yview)
        self.slot_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 图表区：PanedWindow 再次分割为两个图
        chart_paned = ttk.PanedWindow(main_paned, orient=tk.VERTICAL)
        main_paned.add(chart_paned, weight=4)

        self.prefill_frame = ttk.LabelFrame(chart_paned, text="预填充速度 Prefill (t/s)", padding=2)
        chart_paned.add(self.prefill_frame, weight=1)
        self.prefill_canvas = tk.Canvas(self.prefill_frame, bg='#2b2b2b', highlightthickness=0)
        self.prefill_canvas.pack(fill=tk.BOTH, expand=True)
        self.prefill_canvas.bind('<Configure>', lambda e: self._draw_chart())

        self.output_frame = ttk.LabelFrame(chart_paned, text="输出速度 Output (t/s)", padding=2)
        chart_paned.add(self.output_frame, weight=1)
        self.output_canvas = tk.Canvas(self.output_frame, bg='#2b2b2b', highlightthickness=0)
        self.output_canvas.pack(fill=tk.BOTH, expand=True)
        self.output_canvas.bind('<Configure>', lambda e: self._draw_chart())

    def _schedule_draw(self):
        """启动后先安排一次重画；后续只在数据/尺寸变化时重画，不推进时间轴"""
        self._request_draw()
        if self.frame.winfo_exists():
            self.frame.after(2000, self._schedule_draw)

    def _request_draw(self):
        """提交一次重绘，避免短时间内多次入队"""
        if not self._draw_pending:
            self._draw_pending = True
            UiExecutor().submit(self._draw_chart)

    def _on_token_stats(self, stats):
        """收到日志统计时追加采样点"""
        now = time.time()
        changed = False

        p_rate = self._to_float(stats.get('prompt_eval_rate') or stats.get('prompt_speed') or 0)
        if p_rate > 0:
            self._points['prefill'].append({
                't': now,
                'value': p_rate,
                'duration': 0.0,
            })
            changed = True

        g_rate = self._to_float(stats.get('eval_rate') or stats.get('speed') or stats.get('slot_generation') or 0)
        if g_rate > 0:
            self._points['output'].append({
                't': now,
                'value': g_rate,
                'duration': 0.0,
            })
            changed = True

        if changed:
            self._prune_points()
            self._request_draw()

    def stop(self):
        """窗口关闭前移除回调，避免对象泄漏"""
        try:
            self.server_mgr.remove_callback('on_token_stats', self._on_token_stats)
        except Exception:
            pass
        try:
            self.status_monitor.remove_callback(self._on_status)
        except Exception:
            pass

    @staticmethod
    def _to_float(v):
        if isinstance(v, (int, float)):
            return float(v)
        try:
            return float(v)
        except Exception:
            return 0.0

    def _prune_points(self):
        """丢弃超出最大保留时长的旧数据"""
        cutoff = time.time() - self.HISTORY_MAX
        for key in self._points:
            self._points[key] = [p for p in self._points[key] if p['t'] >= cutoff]

    def _draw_chart(self):
        """重画两个图"""
        self._draw_pending = False
        self._draw_metric(self.prefill_canvas, self._points['prefill'], '#00d2ff', 'prefill')
        self._draw_metric(self.output_canvas, self._points['output'], '#ff9f43', 'output')

    def _draw_metric(self, canvas, points, color, key):
        """绘制单张图：采样点之间连线，无新采样时时间轴停住"""
        canvas.delete('all')
        w = canvas.winfo_width() or 300
        h = canvas.winfo_height() or 140
        padding_left = 50
        padding_right = 12
        padding_top = 18
        padding_bottom = 25

        canvas.create_rectangle(0, 0, w, h, fill='#2b2b2b', outline='')

        if not points:
            canvas.create_text(w // 2, h // 2, text='暂无速度数据', fill='white')
            return

        # 视图锚定到最新数据点，不自动随时间前进
        t_latest = max(p['t'] for p in points)
        self._view_end[key] = max(self._view_end[key], t_latest + 1.0)
        view_end = self._view_end[key]
        view_start = view_end - self.HISTORY_WINDOW

        visible = [p for p in points if p['t'] >= view_start]
        if not visible:
            canvas.create_text(w // 2, h // 2, text='暂无速度数据', fill='white')
            return

        values = [p['value'] for p in visible]
        max_val = max(values) * 1.1 or 1.0

        chart_w = w - padding_left - padding_right
        chart_h = h - padding_top - padding_bottom

        # 网格与 Y 轴刻度
        for i in range(5):
            ratio = i / 4.0
            y = h - padding_bottom - ratio * chart_h
            canvas.create_line(padding_left, y, w - padding_right, y, fill='#404040', dash=(2, 4))
            canvas.create_text(padding_left - 6, int(y), text=f'{max_val * ratio:.1f}', fill='gray', anchor='e', font=('Consolas', 8))

        # 计算所有可见点的坐标
        coords = []
        for p in visible:
            x = padding_left + (p['t'] - view_start) / self.HISTORY_WINDOW * chart_w
            y = h - padding_bottom - (p['value'] / max_val) * chart_h
            coords.append((x, y))

        # 点之间用平滑折线连接
        flat = [c for pt in coords for c in pt]
        if len(flat) >= 4:
            canvas.create_line(flat, fill=color, width=2, smooth=True)

        # 绘制每个采样点（只画点，不画线段）
        for p, (x, y) in zip(visible, coords):
            canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline='')

        # 最新值标注
        last = visible[-1]
        canvas.create_text(w - padding_right, padding_top, text=f'{last["value"]:.2f} t/s', fill=color, anchor='ne', font=('Consolas', 9))

        # X 轴标签（相对最新事件）
        canvas.create_text(padding_left, h - 10, text='-60s', fill='gray', anchor='w', font=('Consolas', 8))
        canvas.create_text(w - padding_right, h - 10, text='0', fill='gray', anchor='e', font=('Consolas', 8))

    def _on_status(self, status):
        """状态回调"""
        UiExecutor().submit(self._refresh, status)

    def _refresh(self, status):
        """刷新 UI 状态"""
        if not self.frame.winfo_exists():
            return
        self.status_vars['service_state'].set(status.get('running', False) and '运行中' or '已停止')
        self.status_vars['alive'].set(status.get('alive', False) and '是' or '否')
        self.status_vars['uptime'].set(self._fmt_uptime(status.get('uptime', 0.0)))
        slots = status.get('slots', [])
        self.status_vars['slots'].set(f"{len([s for s in slots if s.get('state') == 'idle']) if slots else 0} / {len(slots)}")
        processing = sum(1 for s in slots if s.get('state') in ('processing', 'generating'))
        self.status_vars['processing'].set(str(processing))
        self.status_vars['error'].set(status.get('last_error') or '-')

        # 更新指示灯
        if status.get('alive', False):
            self.indicator.itemconfig(self._indicator_id, fill='green')
        elif status.get('running', False):
            self.indicator.itemconfig(self._indicator_id, fill='yellow')
        else:
            self.indicator.itemconfig(self._indicator_id, fill='gray')

        # 更新槽位表格
        for item in self.slot_tree.get_children():
            self.slot_tree.delete(item)
        for slot in slots:
            self.slot_tree.insert(
                '', 'end',
                values=(
                    slot.get('id', '-'),
                    slot.get('state', '-'),
                    slot.get('context', '-'),
                    slot.get('prompt_tokens', 0),
                    slot.get('gen_tokens', 0),
                    self._fmt_speed(slot.get('prompt_speed')),
                    self._fmt_speed(slot.get('gen_speed')),
                )
            )

        # 把 /slots 提供的实时速度也加入曲线，让预填充和输出一样有实时曲线
        now = time.time()
        p_speed = self._to_float(status.get('prompt_speed', 0.0))
        g_speed = self._to_float(status.get('gen_speed', 0.0))
        changed = False
        if p_speed > 0 and now - self._last_status_point['prefill'] >= 0.9:
            self._points['prefill'].append({'t': now, 'value': p_speed, 'duration': 0.0})
            self._last_status_point['prefill'] = now
            changed = True
        if g_speed > 0 and now - self._last_status_point['output'] >= 0.9:
            self._points['output'].append({'t': now, 'value': g_speed, 'duration': 0.0})
            self._last_status_point['output'] = now
            changed = True
        if changed:
            self._prune_points()
            self._request_draw()

    def stop(self):
        """窗口关闭前移除回调，避免对象泄漏"""
        try:
            self.server_mgr.remove_callback('on_token_stats', self._on_token_stats)
        except Exception:
            pass
        try:
            self.status_monitor.remove_callback(self._on_status)
        except Exception:
            pass

    @staticmethod
    def _fmt_speed(v):
        if v is None or v == '' or v == 0:
            return '--'
        try:
            return f'{float(v):.2f}'
        except Exception:
            return '--'

    @staticmethod
    def _fmt_uptime(seconds):
        """把秒数格式化为 HH:MM:SS"""
        try:
            s = int(float(seconds))
            h, rem = divmod(s, 3600)
            m, s = divmod(rem, 60)
            return f'{h:02d}:{m:02d}:{s:02d}'
        except Exception:
            return '00:00:00'
