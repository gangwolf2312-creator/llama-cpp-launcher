"""
服务器进程管理器
负责 llama-server 的启动、停止、重启、命令行构建
以及 Token 统计解析
"""
import subprocess
import threading
import time
import re
import os
import signal
import collections
from pathlib import Path


class ServerManager:
    """llama-server 进程管理器"""

    def __init__(self, config_mgr, param_mgr):
        self.config = config_mgr
        self.param_mgr = param_mgr
        self.process = None
        self._lock = threading.RLock()
        self._callbacks_lock = threading.RLock()
        self._running = False
        self._reader_thread = None
        self._start_time = None
        self._starting = False
        self._callbacks = {
            'on_output': [],
            'on_start': [],
            'on_stop': [],
            'on_error': [],
            'on_token_stats': []
        }
        # 最近日志缓冲区，用于诊断
        self._log_buffer = collections.deque(maxlen=500)
        # Token 统计正则
        self._token_patterns = {
            'prompt_eval_time': re.compile(r'prompt eval time\s*=\s*([\d.]+)\s*ms'),
            'prompt_eval_rate': re.compile(r'prompt eval(?:\s+(?:rate|time)).*?(\d+(?:\.\d+)?)\s*(?:tokens?\s*(?:/s|/sec|per second|per sec)|t/s|tok/s)\b'),
            'eval_time': re.compile(r'(?<!prompt\s)eval\s+time\s*=\s*([\d.]+)\s*ms'),
            'eval_rate': re.compile(r'(?<!prompt\s)eval(?:\s+(?:rate|time)).*?(\d+(?:\.\d+)?)\s*(?:tokens?\s*(?:/s|/sec|per second|per sec)|t/s|tok/s)\b'),
            'total_tokens': re.compile(r'([\d.]+)\s*tokens/s\s*\|\s*total\s*=\s*(\d+)'),
            'gpu_info': re.compile(r'(AMD|NVIDIA|Intel).*?(\w+)'),
            'slot_prompt': re.compile(r'prompt\s+processing.*?(\d+(?:\.\d+)?)\s*(?:tokens?\s*(?:/s|/sec|per second|per sec)|t/s|tok/s)\b'),
            'slot_generation': re.compile(r'(?:tg|generation)\s*[:=]\s*(\d+(?:\.\d+)?)\s*(?:tokens?\s*(?:/s|/sec|per second|per sec)|t/s|tok/s)\b'),
        }

    def set_callback(self, event, callback):
        """注册事件回调；支持多个回调同时生效"""
        if event not in self._callbacks or callback is None:
            return
        with self._callbacks_lock:
            if callback not in self._callbacks[event]:
                self._callbacks[event].append(callback)

    def remove_callback(self, event, callback):
        """移除指定事件回调"""
        if event not in self._callbacks:
            return
        with self._callbacks_lock:
            try:
                self._callbacks[event].remove(callback)
            except ValueError:
                pass

    def _emit(self, event, *args):
        """触发指定事件的所有回调"""
        with self._callbacks_lock:
            callbacks = list(self._callbacks.get(event, []))
        for cb in callbacks:
            try:
                cb(*args)
            except Exception:
                pass

    def build_command(self):
        """构建完整启动命令"""
        server_path = self.config.get("paths.llama_server", "")
        if not server_path:
            raise ValueError("\u672a\u8bbe\u7f6e llama-server \u8def\u5f84")

        server_exe = Path(server_path)
        if not server_exe.exists():
            # 尝试自动查找 llama-server.exe
            if server_exe.is_dir():
                candidates = list(server_exe.glob("llama-server*"))
                if candidates:
                    server_exe = candidates[0]
                else:
                    raise ValueError(f"\u5728 {server_path} \u4e2d\u672a\u627e\u5230 llama-server")

        cmd = [str(server_exe)]

        # 添加模型参数
        model_path = self.config.get("paths.last_model", "")
        if not model_path:
            # 尝试从启动配置获取
            model_path = self.config.get("server.last_model", "")
        if model_path and Path(model_path).exists():
            cmd.extend(["--model", str(model_path)])

        # 添加 MTP/推测解码模型
        mtp_path = self.config.get("paths.mtp_model", "")
        if mtp_path and Path(mtp_path).exists():
            cmd.extend(["--model-draft", str(mtp_path)])

        # 添加视觉模型 MMProj：只加载与当前模型同目录的 mmproj，避免误加载其他模型的视觉文件
        vision_path = self.config.get("paths.vision_model", "")
        model_path = self.config.get("paths.last_model") or self.config.get("server.last_model", "")
        if vision_path and Path(vision_path).exists() and model_path and Path(model_path).exists():
            try:
                vision_parent = Path(vision_path).parent.resolve()
                model_parent = Path(model_path).parent.resolve()
                if vision_parent == model_parent:
                    cmd.extend(["--mmproj", str(vision_path)])
            except Exception:
                pass

        # 添加文件路径参数
        file_params = [
            ("log_file", "--log-file"),
            ("lora_file", "--lora"),
            ("control_vector_file", "--control-vector"),
            ("grammar_file", "--grammar-file"),
            ("json_schema_file", "--json-schema"),
        ]
        for config_key, arg_name in file_params:
            val = self.config.get(f"paths.{config_key}", "")
            if val and str(val).strip():
                cmd.extend([arg_name, str(val).strip()])

        # 添加动态参数系统的参数
        dynamic_args = self.param_mgr.build_command_line(self.config)
        cmd.extend(dynamic_args)

        # 添加自定义网页 UI 目录（覆盖内置 UI）
        webui_path = self.config.get("paths.webui_path", "")
        if webui_path and Path(webui_path).exists() and Path(webui_path).is_dir():
            cmd.extend(["--path", str(webui_path)])

        return cmd

    def start(self):
        """启动 llama-server"""
        with self._lock:
            if self._starting or (self.process and self.process.poll() is None):
                return False, "\u670d\u52a1\u5df2\u5728\u8fd0\u884c\u6216\u6b63\u5728\u542f\u52a8"
            self._starting = True

        try:
            cmd = self.build_command()
        except ValueError as e:
            with self._lock:
                self._starting = False
            return False, str(e)

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True,
                bufsize=1,
                encoding='utf-8',
                errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
        except Exception as e:
            with self._lock:
                self._starting = False
            self._emit('on_error', str(e))
            return False, str(e)

        with self._lock:
            # 如果并发启动时另一个线程已经占坑，杀掉当前进程
            if self.process and self.process.poll() is None:
                try:
                    process.terminate()
                except Exception:
                    pass
                self._starting = False
                return False, "\u670d\u52a1\u5df2\u88ab\u5176\u4ed6\u7ebf\u7a0b\u542f\u52a8"
            self.process = process
            self._running = True
            self._start_time = time.time()
            self._starting = False

        # 启动输出读取线程
        self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self._reader_thread.start()

        self._emit('on_start', cmd)
        return True, "\u542f\u52a8\u6210\u529f"

    def stop(self):
        """停止 llama-server（Windows 下强制结束进程树，避免残留）"""
        stopped = False
        with self._lock:
            proc = self.process
            if proc and proc.poll() is None:
                pid = proc.pid
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            proc.kill()
                            proc.wait(timeout=3)
                        except Exception:
                            pass
                        # Windows 兜底：强制结束整个进程树
                        if os.name == 'nt':
                            try:
                                subprocess.call(
                                    ['taskkill', '/F', '/T', '/PID', str(pid)],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                    creationflags=subprocess.CREATE_NO_WINDOW
                                )
                            except Exception:
                                pass
                except Exception:
                    pass
                finally:
                    self.process = None
                    self._running = False
                    self._start_time = None
                    stopped = True
        if stopped:
            self._emit('on_stop')
        return stopped

    def restart(self):
        """重启 llama-server"""
        self.stop()
        # 等待旧进程彻底退出，最多 5 秒
        for _ in range(50):
            if not self.process or self.process.poll() is not None:
                break
            time.sleep(0.1)
        return self.start()

    def is_running(self):
        """检查服务是否运行（线程安全）"""
        with self._lock:
            return self.process is not None and self.process.poll() is None

    def get_start_time(self):
        """获取服务启动时间戳"""
        with self._lock:
            return self._start_time

    def get_pid(self):
        """获取进程ID（线程安全）"""
        with self._lock:
            if self.process:
                return self.process.pid
            return None

    def get_recent_logs(self, n=200):
        """获取最近 n 行服务器输出，用于诊断"""
        with self._lock:
            return list(self._log_buffer)[-n:]

    def _read_output(self):
        """后台读取进程输出"""
        if not self.process or not self.process.stdout:
            return

        ansi_escape = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
        buffer = []
        for line in iter(self.process.stdout.readline, ''):
            if not self._running:
                break
            line = line.rstrip('\n')
            line = ansi_escape.sub('', line)
            buffer.append(line)

            # 保存到日志缓冲区用于诊断
            self._log_buffer.append(line)

            # 发送输出到回调
            self._emit('on_output', line)

            # 解析Token统计
            self._parse_token_stats(line)

        # 进程结束
        with self._lock:
            self._running = False
            self._start_time = None
        if self.process:
            try:
                self.process.stdout.close()
            except Exception:
                pass

        self._emit('on_stop')

    def _parse_token_stats(self, line):
        """从输出行解析 Token 统计信息"""
        stats = {}
        for key, pattern in self._token_patterns.items():
            match = pattern.search(line)
            if match:
                if key in ('prompt_eval_time', 'eval_time'):
                    stats[key] = float(match.group(1))
                elif key in ('prompt_eval_rate', 'eval_rate', 'slot_prompt', 'slot_generation'):
                    stats[key] = float(match.group(1))
                elif key == 'total_tokens':
                    stats['speed'] = float(match.group(1))
                    stats['total'] = int(match.group(2))

        # 兜底：如果只有 time 没有 rate，从 ms 和 token 数计算 rate
        if 'prompt_eval_time' in stats and 'prompt_eval_rate' not in stats:
            pm = re.search(r'prompt eval time\s*=\s*[\d.]+\s*ms\s*/\s*(\d+)\s*tokens', line)
            if pm and stats['prompt_eval_time'] > 0:
                stats['prompt_eval_rate'] = int(pm.group(1)) / (stats['prompt_eval_time'] / 1000.0)
        if 'eval_time' in stats and 'eval_rate' not in stats:
            em = re.search(r'(?<!prompt\s)eval\s+time\s*=\s*[\d.]+\s*ms\s*/\s*(\d+)\s*tokens', line)
            if em and stats['eval_time'] > 0:
                stats['eval_rate'] = int(em.group(1)) / (stats['eval_time'] / 1000.0)

        if stats:
            self._emit('on_token_stats', stats)

    def generate_bat_script(self, filepath=None):
        """生成启动脚本 .bat 文件"""
        try:
            cmd = self.build_command()
        except ValueError:
            return None

        if filepath is None:
            desktop = Path.home() / "Desktop"
            filepath = desktop / "start-llama-server.bat"

        bat_content = f"@echo off\n"
        bat_content += f"chcp 65001 >nul\n"
        bat_content += f"title Llama.cpp Server\n"
        bat_content += f"echo Starting llama-server...\n"
        bat_content += f"echo Command: {' '.join(cmd)}\n"
        bat_content += f"echo.\n"
        bat_content += f"\"{cmd[0]}\" {' '.join(f'"{a}"' for a in cmd[1:])}\n"
        bat_content += f"echo.\n"
        bat_content += f"pause\n"

        try:
            with open(filepath, 'w', encoding='gbk') as f:
                f.write(bat_content)
            return filepath
        except Exception:
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(bat_content)
                return filepath
            except Exception as e:
                print(f"[ServerManager] BAT script error: {e}")
                return None
