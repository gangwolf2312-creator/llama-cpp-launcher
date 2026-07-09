"""
配置管理器 - 单例模式
管理 config.json 和 system.json 的读写
"""
import json
import os
import time
import threading
from pathlib import Path


class ConfigManager:
    """配置管理器单例"""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, app_dir=None):
        if self._initialized:
            return
        self._initialized = True

        self.app_dir = Path(app_dir) if app_dir else Path(os.path.dirname(os.path.abspath(__file__))).parent
        self.config_path = self.app_dir / "config.json"
        self.system_path = self.app_dir / "system.json"
        self.logs_dir = self.app_dir / "logs"
        self.logs_dir.mkdir(exist_ok=True)

        self._config = {}
        self._system = {}
        self._lock = threading.RLock()

        self._load_all()

    def _load_all(self):
        """加载所有配置文件"""
        self._config = self._load_json(self.config_path, self._default_config())
        self._system = self._load_json(self.system_path, self._default_system())
        # 迁移旧配置：spec-type 的非法值 draft -> draft-mtp
        try:
            param_values = self._system.get('param_values', {})
            if param_values.get('spec-type') == 'draft':
                param_values['spec-type'] = 'draft-mtp'
        except Exception:
            pass

        # 迁移旧配置：log-colors 从 bool 改为 enum（on/off/auto）
        try:
            param_values = self._system.get('param_values', {})
            v = param_values.get('log-colors')
            if v is True:
                param_values['log-colors'] = 'on'
            elif v is False or v is None:
                param_values['log-colors'] = 'auto'
        except Exception:
            pass

    def _load_json(self, path, defaults):
        """加载JSON文件,带默认值合并"""
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                # 合并默认值(处理新增字段)
                return self._merge_defaults(data, defaults)
            except Exception:
                return defaults
        return defaults

    def _merge_defaults(self, data, defaults):
        """递归合并默认值"""
        if isinstance(defaults, dict):
            result = dict(defaults)
            if isinstance(data, dict):
                for k, v in data.items():
                    if k in result:
                        result[k] = self._merge_defaults(v, result[k])
                    else:
                        result[k] = v
            return result
        return data if data is not None else defaults

    def _default_config(self):
        """config.json 默认值"""
        return {
            "app": {
                "version": "3.0",
                "theme": "darkly",
                "language": "zh-CN"
            },
            "paths": {
                "llama_server": "",
                "model_library": "",
                "vision_model": "",
                "mtp_model": "",
                "chat_template_file": "",
                "log_file": "",
                "lora_file": "",
                "control_vector_file": "",
                "grammar_file": "",
                "json_schema_file": "",
                "webui_path": "C:\\Users\\gangw\\Downloads\\llama-b9870-ui\\llama-b9870"
            },
            "ui": {
                "window_width": 1400,
                "window_height": 900,
                "splitter_distance_left": 420,
                "splitter_distance_right_log": 500,
                "last_tab": 0
            },
            "server": {
                "auto_start": False,
                "last_model": "",
                "last_preset": "默认助手"
            },
            "api": {
                "url": "http://127.0.0.1:8080/v1",
                "key": "",
                "model_id": "",
                "mode": "local"
            },
            "system_prompts": {
                "默认助手": "You are a helpful assistant.",
                "代码专家": "You are an expert programmer. Write clean, efficient, well-documented code.",
                "创意写作": "You are a creative writing assistant. Help with storytelling, character development, and vivid descriptions.",
                "公文严谨": "You are a professional document assistant. Provide precise, formal, and well-structured responses suitable for official documents.",
                "Kimi预设-精确模式": "You are Kimi, an AI assistant developed by Moonshot AI. Provide accurate, concise, and well-reasoned responses."
            },
            "temperature_presets": {
                "精确公文": {
                    "name": "精确公文",
                    "description": "接近贪婪解码，术语精准，几乎无发散。适合法规引用、技术参数、数据提取。",
                    "temperature": 0.1, "top_k": 10, "top_p": 1.0, "min_p": 0.1,
                    "repeat_penalty": 1.1, "repeat_last_n": 128,
                    "mirostat_mode": 0, "mirostat_tau": 5.0, "mirostat_eta": 0.1,
                    "frequency_penalty": 0.0, "presence_penalty": 0.0
                },
                "标准平衡": {
                    "name": "标准平衡",
                    "description": "llama.cpp 官方默认风格，流畅自然，适合大多数通用场景。",
                    "temperature": 0.7, "top_k": 40, "top_p": 0.9, "min_p": 0.05,
                    "repeat_penalty": 1.0, "repeat_last_n": 64,
                    "mirostat_mode": 0, "mirostat_tau": 5.0, "mirostat_eta": 0.1,
                    "frequency_penalty": 0.0, "presence_penalty": 0.0
                },
                "创意写作": {
                    "name": "创意写作",
                    "description": "高温 + Min-P 硬兜底，有灵气不套路。适合标题、导语、头脑风暴。",
                    "temperature": 1.5, "top_k": 0, "top_p": 1.0, "min_p": 0.1,
                    "repeat_penalty": 1.0, "repeat_last_n": 64,
                    "mirostat_mode": 0, "mirostat_tau": 5.0, "mirostat_eta": 0.1,
                    "frequency_penalty": 0.0, "presence_penalty": 0.0
                },
                "推理探索": {
                    "name": "推理探索",
                    "description": "高温仍保持逻辑连贯，适合让模型换角度审视问题、多方案比选。",
                    "temperature": 2.0, "top_k": 0, "top_p": 1.0, "min_p": 0.05,
                    "repeat_penalty": 1.0, "repeat_last_n": 64,
                    "mirostat_mode": 0, "mirostat_tau": 5.0, "mirostat_eta": 0.1,
                    "frequency_penalty": 0.0, "presence_penalty": 0.0
                },
                "长文本稳定": {
                    "name": "长文本稳定",
                    "description": "Mirostat V2 自动控温，维持长文本前后风格一致。适合一口气写完章节。",
                    "temperature": 0.8, "top_k": 40, "top_p": 0.9, "min_p": 0.05,
                    "repeat_penalty": 1.0, "repeat_last_n": 64,
                    "mirostat_mode": 2, "mirostat_tau": 5.0, "mirostat_eta": 0.1,
                    "frequency_penalty": 0.0, "presence_penalty": 0.0
                },
                "规划润色": {
                    "name": "规划润色",
                    "description": "中高温润色 + 大窗口重复惩罚，打磨正式规划文本。",
                    "temperature": 0.9, "top_k": 0, "top_p": 0.95, "min_p": 0.05,
                    "repeat_penalty": 1.15, "repeat_last_n": 256,
                    "mirostat_mode": 0, "mirostat_tau": 5.0, "mirostat_eta": 0.1,
                    "frequency_penalty": 0.0, "presence_penalty": 0.0
                }
            }
        }

    def _default_system(self):
        """system.json 默认值 - 参数启用状态"""
        return {
            "params_enabled": {},
            "param_values": {},
            "param_presets": {
                "default": {}
            },
            "model_params": {}
        }

    def get(self, key, default=None, scope="config"):
        """获取配置值"""
        with self._lock:
            data = self._config if scope == "config" else self._system
            keys = key.split('.') if isinstance(key, str) else key
            for k in keys:
                if isinstance(data, dict) and k in data:
                    data = data[k]
                else:
                    return default
            return data

    def set(self, key, value, scope="config"):
        """设置配置值"""
        with self._lock:
            data = self._config if scope == "config" else self._system
            keys = key.split('.') if isinstance(key, str) else key
            for k in keys[:-1]:
                if k not in data:
                    data[k] = {}
                data = data[k]
            data[keys[-1]] = value

    def save(self, scope=None):
        """保存配置到文件"""
        with self._lock:
            if scope is None or scope == "config":
                self._save_json(self.config_path, self._config)
            if scope is None or scope == "system":
                self._save_json(self.system_path, self._system)

    def _save_json(self, path, data):
        """保存JSON文件"""
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ConfigManager] Save error: {e}")

    def get_config(self):
        """获取完整配置字典"""
        with self._lock:
            return dict(self._config)

    def get_system(self):
        """获取完整系统参数字典"""
        with self._lock:
            return dict(self._system)

    def set_system(self, data):
        """设置完整系统参数"""
        with self._lock:
            self._system = data

    def is_param_enabled(self, param_name):
        """检查参数是否启用"""
        enabled = self.get("params_enabled", {}, "system")
        return enabled.get(param_name, False)

    def set_param_enabled(self, param_name, enabled):
        """设置参数启用状态"""
        params = self.get("params_enabled", {}, "system")
        params[param_name] = enabled
        self.set("params_enabled", params, "system")

    def get_param_value(self, param_name, default=None):
        """获取参数当前值"""
        values = self.get("param_values", {}, "system")
        return values.get(param_name, default)

    def set_param_value(self, param_name, value):
        """设置参数当前值"""
        values = self.get("param_values", {}, "system")
        values[param_name] = value
        self.set("param_values", values, "system")

    def get_params_enabled_all(self):
        """获取所有参数启用状态"""
        return dict(self.get("params_enabled", {}, "system"))

    def get_param_values_all(self):
        """获取所有参数当前值"""
        return dict(self.get("param_values", {}, "system"))

    def set_params_enabled_all(self, enabled):
        """批量设置参数启用状态"""
        self.set("params_enabled", dict(enabled), "system")

    def set_param_values_all(self, values):
        """批量设置参数当前值"""
        self.set("param_values", dict(values), "system")

    def save_model_params(self, model_path):
        """保存当前参数为该模型的历史成功启动参数"""
        model_path = str(model_path).strip()
        if not model_path:
            return
        model_params = self.get("model_params", {}, "system")
        model_params[model_path] = {
            "enabled": self.get_params_enabled_all(),
            "values": self.get_param_values_all(),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.set("model_params", model_params, "system")

    def get_model_params(self, model_path):
        """获取指定模型的历史成功启动参数"""
        model_params = self.get("model_params", {}, "system")
        return model_params.get(str(model_path).strip())

    def load_model_params(self, model_path):
        """加载指定模型的历史参数到当前配置，返回是否成功"""
        record = self.get_model_params(model_path)
        if not record:
            return False
        if "enabled" in record:
            self.set_params_enabled_all(record["enabled"])
        if "values" in record:
            self.set_param_values_all(record["values"])
        return True


# 全局快捷访问函数
def get_config_mgr(app_dir=None):
    """获取 ConfigManager 单例实例"""
    return ConfigManager(app_dir)
