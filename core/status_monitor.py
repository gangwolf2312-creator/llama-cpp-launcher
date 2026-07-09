import json
import threading
import time
import urllib.request
import urllib.error


class StatusMonitor:
    """llama-server 运行状态监控器"""

    def __init__(self, server_manager, host='127.0.0.1', port=8080, interval=1.0):
        self.server_mgr = server_manager
        self.host = host
        self.port = port
        self.interval = interval
        self._running = False
        self._thread = None
        self._lock = threading.RLock()
        self._callbacks = []
        self._token_speed = 0.0
        self._prompt_speed = 0.0
        self._gen_speed = 0.0
        self._status = {
            'running': False,
            'alive': False,
            'start_time': None,
            'uptime': 0.0,
            'slots': [],
            'total_slots': 0,
            'idle_slots': 0,
            'processing_slots': 0,
            'global_speed': 0.0,
            'error': None,
        }
        self._last_slot_state = {}
        self._last_token_stats_time = 0.0
        # 注册日志 token 统计回调作为补充速度来源
        self.server_mgr.set_callback('on_token_stats', self._on_token_stats)

    def set_host_port(self, host, port):
        """动态更新监控地址"""
        with self._lock:
            self.host = host
            self.port = port

    def add_callback(self, callback):
        """注册状态变更回调(callback(status))"""
        with self._lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

    def remove_callback(self, callback):
        """移除状态变更回调"""
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def start(self):
        """启动监控线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止监控线程"""
        self._running = False
        # 移除对 server token 统计的监听，避免服务停止后仍被回调
        try:
            self.server_mgr.remove_callback('on_token_stats', self._on_token_stats)
        except Exception:
            pass
        if self._thread:
            try:
                self._thread.join(timeout=2.0)
            except Exception:
                pass
            self._thread = None

    def get_status(self):
        """获取当前状态快照"""
        with self._lock:
            return dict(self._status)

    def _on_token_stats(self, stats):
        """接收日志解析出的 token 速度，作为 /slots 的兜底；记录接收时间用于超时清零"""
        prompt_speed = stats.get('prompt_eval_rate') or 0
        gen_speed = (
            stats.get('eval_rate') or
            stats.get('speed') or
            stats.get('slot_generation') or
            0
        )
        try:
            prompt_speed = float(prompt_speed) if prompt_speed else 0.0
            gen_speed = float(gen_speed) if gen_speed else 0.0
        except Exception:
            prompt_speed = 0.0
            gen_speed = 0.0
        with self._lock:
            self._token_speed = gen_speed
            self._prompt_speed = prompt_speed
            self._gen_speed = gen_speed
            self._last_token_stats_time = time.time()

    def _loop(self):
        """轮询循环"""
        while self._running:
            self._poll()
            time.sleep(self.interval)

    def _poll(self):
        """单次轮询"""
        # 服务进程是否在运行
        if not self.server_mgr.is_running():
            self._last_slot_state.clear()
            self._update(
                running=False,
                alive=False,
                start_time=None,
                uptime=0.0,
                slots=[],
                total_slots=0,
                idle_slots=0,
                processing_slots=0,
                prompt_speed=0.0,
                gen_speed=0.0,
                global_speed=0.0,
                error=None,
            )
            return

        start_time = self.server_mgr.get_start_time()
        uptime = time.time() - start_time if start_time else 0.0

        with self._lock:
            host = self.host
            port = self.port

        base_url = f'http://{host}:{port}'
        alive = False
        error = None

        # 1. 健康检查：/health，不存在则降级到 /v1/models
        try:
            req = urllib.request.Request(f'{base_url}/health', method='GET')
            with urllib.request.urlopen(req, timeout=2) as resp:
                alive = resp.status == 200
        except Exception as e:
            try:
                req = urllib.request.Request(f'{base_url}/v1/models', method='GET')
                with urllib.request.urlopen(req, timeout=2) as resp:
                    alive = resp.status == 200
            except Exception as e2:
                error = str(e2)

        # 2. 获取槽位信息
        slots = []
        total_slots = 0
        idle_slots = 0
        processing_slots = 0
        now = time.time()
        if alive:
            try:
                req = urllib.request.Request(f'{base_url}/slots', method='GET')
                with urllib.request.urlopen(req, timeout=2) as resp:
                    data = json.loads(resp.read().decode('utf-8', errors='ignore'))
                    slots = self._parse_slots(data, now=now)
                    total_slots = len(slots)
                    idle_slots = sum(1 for s in slots if s.get('state') == 'idle')
                    processing_slots = total_slots - idle_slots
            except Exception:
                # /slots 不存在或解析失败，不是致命错误
                pass

        # 3. 从 /slots 聚合实时速度；如果没有 /slots 数据，再用日志解析兜底
        prompt_speed = 0.0
        gen_speed = 0.0
        if slots:
            p_speeds = [s['prompt_speed'] for s in slots if s.get('prompt_speed', 0.0) > 0]
            g_speeds = [s['gen_speed'] for s in slots if s.get('gen_speed', 0.0) > 0]
            prompt_speed = sum(p_speeds) / len(p_speeds) if p_speeds else 0.0
            gen_speed = sum(g_speeds) / len(g_speeds) if g_speeds else 0.0
        else:
            with self._lock:
                log_prompt = self._prompt_speed
                log_gen = self._gen_speed
                last_log = self._last_token_stats_time
            # 只有 2 秒内有日志统计才使用，否则认为已经空闲，速度清零
            if now - last_log <= 2.0:
                prompt_speed = log_prompt
                gen_speed = log_gen
            else:
                prompt_speed = 0.0
                gen_speed = 0.0
        global_speed = gen_speed

        if not slots and alive:
            slots = [{
                'id': 0,
                'state': 'processing' if global_speed > 0 else 'idle',
                'ctx': 0,
                'prompt_tokens': 0,
                'gen_tokens': 0,
                'prompt_speed': 0.0,
                'gen_speed': global_speed,
            }]
            total_slots = 1
            idle_slots = 0 if global_speed > 0 else 1
            processing_slots = 1 - idle_slots

        self._update(
            running=True,
            alive=alive,
            start_time=start_time,
            uptime=uptime,
            slots=slots,
            total_slots=total_slots,
            idle_slots=idle_slots,
            processing_slots=processing_slots,
            global_speed=global_speed,
            prompt_speed=prompt_speed,
            gen_speed=gen_speed,
            error=error,
        )

    def _update(self, **kwargs):
        """原子更新状态并通知回调"""
        with self._lock:
            self._status.update(kwargs)
            status = dict(self._status)
            callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb(status)
            except Exception:
                pass

    def _parse_slots(self, data, now=None):
        """解析 /slots 返回，兼容不同字段名；并基于前后两次采样计算增量速度"""
        if now is None:
            now = time.time()
        slots = []
        if isinstance(data, dict):
            data = data.get('slots', [])
        if not isinstance(data, list):
            return slots

        for slot in data:
            try:
                # 槽位 ID
                slot_id = self._pick(slot, 'id', 'slot_id', 'slot', 'index') or 0

                # 状态
                raw_state = self._pick(slot, 'state', 'status')
                is_processing = self._pick(slot, 'is_processing', 'processing', 'busy')
                if raw_state:
                    state = 'idle' if raw_state in ('idle', 'IDLE') else 'processing'
                elif isinstance(is_processing, bool):
                    state = 'processing' if is_processing else 'idle'
                else:
                    state = 'processing'

                # prompt tokens
                prompt_tokens = self._pick(
                    slot, 'n_prompt_tokens', 'n_prompt_tokens_processed',
                    'prompt_tokens', 'n_prompt'
                ) or 0

                # generated tokens
                gen_tokens = self._pick(
                    slot, 'n_decoded', 'n_predicted', 'n_tokens', 'generated_tokens'
                ) or 0

                # 上下文大小
                ctx = self._pick(slot, 'n_ctx', 'context', 'ctx_size', 'n_ctx_size') or 0

                # 生成速度 (tok/s)，优先拿现成的 prompt/gen per-second
                prompt_speed = self._pick(
                    slot, 'prompt_per_second', 'prompt_rate', 'prompt_tok_s'
                ) or 0.0
                gen_speed = self._pick(
                    slot, 'predicted_per_second', 'eval_rate', 'eval_per_second',
                    't_token_generation', 'generation_rate', 'tok_s'
                ) or 0.0

                # t_token_generation 等字段可能是 ms/token，需要转成 tok/s
                if gen_speed and isinstance(gen_speed, (int, float)) and gen_speed > 1000:
                    gen_speed = 1000.0 / gen_speed

                # 空闲槽位速度强制为 0；处理中槽位根据前后两次采样算增量速度
                if state == 'idle':
                    prompt_speed = 0.0
                    gen_speed = 0.0
                else:
                    prev = self._last_slot_state.get(slot_id)
                    if prev:
                        dt = now - prev.get('time', now)
                        if dt > 0:
                            if prompt_tokens > prev.get('prompt_tokens', 0):
                                prompt_speed = (prompt_tokens - prev.get('prompt_tokens', 0)) / dt
                            elif prompt_speed and not prompt_tokens:
                                prompt_speed = 0.0
                            if gen_tokens > prev.get('gen_tokens', 0):
                                gen_speed = (gen_tokens - prev.get('gen_tokens', 0)) / dt
                            elif gen_speed and not gen_tokens:
                                gen_speed = 0.0

                # 更新槽位历史状态
                self._last_slot_state[slot_id] = {
                    'time': now,
                    'prompt_tokens': prompt_tokens,
                    'gen_tokens': gen_tokens,
                }

                slots.append({
                    'id': slot_id,
                    'state': state,
                    'context': ctx,
                    'prompt_tokens': prompt_tokens,
                    'gen_tokens': gen_tokens,
                    'prompt_speed': prompt_speed,
                    'gen_speed': gen_speed,
                })
            except Exception:
                # 单个槽位解析失败，跳过
                continue
        return slots

    @staticmethod
    def _pick(obj, *keys):
        """从 dict 中按候选 key 取第一个存在的值"""
        if not isinstance(obj, dict):
            return None
        for k in keys:
            if k in obj:
                return obj[k]
        return None
