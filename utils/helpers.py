"""
工具函数集
"""
import os
import re
import json
from pathlib import Path
from tkinter import filedialog, messagebox
from utils.gguf_utils import gguf_context_length, gguf_model_type, gguf_has_mtp

# 上下文长度只保留 32k/64k/128k/256k 四个档位
CTX_GEARS = [32768, 65536, 131072, 262144]


def clamp_ctx_to_gears(ctx, gears=CTX_GEARS):
    """把上下文长度对齐到最近的可用档位，且不超过原始值"""
    if not gears:
        return ctx
    try:
        ctx = int(ctx)
    except Exception:
        ctx = 0
    valid = [g for g in gears if g <= ctx]
    if valid:
        return max(valid)
    return gears[0]


def browse_file(title="\u9009\u62e9\u6587\u4ef6", filetypes=None, initialdir=""):
    """文件浏览对话框"""
    if filetypes is None:
        filetypes = [("All files", "*.*")]
    path = filedialog.askopenfilename(
        title=title,
        filetypes=filetypes,
        initialdir=initialdir if initialdir else None
    )
    return path if path else None


def browse_directory(title="\u9009\u62e9\u6587\u4ef6\u5939", initialdir=""):
    """文件夹浏览对话框"""
    path = filedialog.askdirectory(
        title=title,
        initialdir=initialdir if initialdir else None
    )
    return path if path else None


def find_best_model_library(root, max_depth=3):
    """
    在 root 下查找包含最多 .gguf 模型的子目录。
    用于用户选择父目录时自动定位到真正的模型库目录。
    返回最佳目录路径（Path），找不到则返回 None。
    """
    root_path = Path(root)
    if not root_path.exists():
        return None

    counts = {}
    for pattern in ('*.gguf', '*.GGUF'):
        for fpath in root_path.rglob(pattern):
            try:
                rel = fpath.parent.relative_to(root_path)
                # 只考虑到 max_depth 的层级，避免把过深的单个文件目录当成根
                parts = rel.parts[:max_depth]
                branch = root_path.joinpath(*parts)
                counts[branch] = counts.get(branch, 0) + 1
            except Exception:
                continue

    if not counts:
        return None
    return max(counts, key=counts.get)


def scan_models(directory, min_size_gb=0.5):
    """
    扫描目录中的模型文件
    返回: [{name, path, size, category}, ...]
    category: text / vision / mtp
    """
    models = []
    seen = set()
    dir_path = Path(directory)
    if not dir_path.exists():
        return models

    min_size = min_size_gb * 1024**3  # bytes

    vision_patterns = [
        r'(?:llava|minicpm|internvl|qwen.*vl|phi.*vision|aya.*vision|mistral.*pixtral)',
        r'(?:vision|visual|multimodal)'
    ]
    mtp_patterns = [
        r'(?:draft|mtp|speculative|small|tiny)',
        r'(?:\d+b.*q4|\d+b.*q3|\d+b.*q2)'
    ]

    for ext in ['*.gguf', '*.GGUF']:
        for fpath in dir_path.rglob(ext):
            try:
                abs_path = fpath.resolve()
                if str(abs_path) in seen:
                    continue
                seen.add(str(abs_path))

                size = fpath.stat().st_size
                if size < min_size:
                    continue

                name = fpath.name
                lower_name = name.lower()

                # 分类
                category = 'text'
                for p in vision_patterns:
                    if re.search(p, lower_name):
                        category = 'vision'
                        break
                if category == 'text':
                    for p in mtp_patterns:
                        if re.search(p, lower_name) and size < 5 * 1024**3:  # MTP通常较小
                            category = 'mtp'
                            break

                models.append({
                    'name': name,
                    'path': str(fpath),
                    'size': size,
                    'size_gb': round(size / (1024**3), 2),
                    'category': category,
                    'parent': str(fpath.parent.relative_to(dir_path)) if fpath.parent != dir_path else ''
                })
            except Exception:
                continue

    models.sort(key=lambda x: (x['category'], x['name']))
    return models


def format_size(size_bytes):
    """格式化文件大小"""
    if size_bytes >= 1024**3:
        return f"{size_bytes/(1024**3):.2f} GB"
    elif size_bytes >= 1024**2:
        return f"{size_bytes/(1024**2):.1f} MB"
    else:
        return f"{size_bytes/1024:.1f} KB"


def detect_model_info(path, vision_path='', mtp_path=''):
    """检测模型信息
    - 参数量从文件名提取
    - 推荐上下文优先从文件名/GGUF元数据读取, 否则给一个安全默认值
    - 视觉/MTP 状态只根据当前模型本身判断, 不因为全局配置就一刀切
    """
    info = {
        'param_count': 'unknown',
        'recommended_ctx': 4096,
        'is_vision': False,
        'is_mtp': False,
        'is_moe': False,
        'mmproj_path': '',
    }

    model_path = Path(path)
    name = model_path.name.lower()
    parent_dir = model_path.parent

    # 参数量: 取文件名中第一个出现的 Xb/XB
    param_match = re.search(r'(\d+\.?\d*)b', name)
    if param_match:
        info['param_count'] = param_match.group(0).upper()

    # 上下文长度: 先读文件名中的上下文标识
    ctx_match = re.search(r'(?:^|[^a-zA-Z0-9.])(\d+\.?\d*)\s*(k|m)(?:$|[^a-zA-Z0-9])', name, re.IGNORECASE)
    if ctx_match:
        num = float(ctx_match.group(1))
        unit = ctx_match.group(2).lower()
        if unit == 'k':
            info['recommended_ctx'] = int(num * 1024)
        elif unit == 'm':
            info['recommended_ctx'] = int(num * 1024 * 1024)
    else:
        # 读 GGUF 元数据里的真实最大上下文
        try:
            gguf_ctx = gguf_context_length(path)
            if gguf_ctx:
                info['recommended_ctx'] = gguf_ctx
        except Exception:
            pass

    # 从 GGUF 元数据读取模型类型（稠密/MoE）和 MTP 层信息
    try:
        info['is_moe'] = (gguf_model_type(path) == 'moe')
        info['is_mtp'] = gguf_has_mtp(path)
    except Exception:
        pass

    # 视觉模型: 文件名含常见视觉关键词, 或者同目录下存在 mmproj 文件, 或者配置的 vision 路径就在同目录
    vision_keywords = ['llava', 'minicpm', 'internvl', 'qwen2-vl', 'qwen2.5-vl',
                       'vision', 'bakllava', 'yi-vl', 'gemma-3-it']
    mmproj_candidates = list(parent_dir.glob('mmproj*.gguf'))
    if mmproj_candidates:
        info['is_vision'] = True
        info['mmproj_path'] = str(mmproj_candidates[0])
    elif any(k in name for k in vision_keywords):
        info['is_vision'] = True
    elif vision_path and str(vision_path).strip():
        try:
            vision_path = Path(vision_path)
            if str(vision_path.parent.resolve()).lower() == str(parent_dir.resolve()).lower():
                info['is_vision'] = True
                info['mmproj_path'] = str(vision_path)
        except Exception:
            pass

    # MTP/草稿模型: 文件名含 mtp/draft 关键词, 或者当前模型本身就是配置的 MTP 路径
    mtp_keywords = ['mtp', 'draft', 'speculative']
    if any(k in name for k in mtp_keywords):
        info['is_mtp'] = True
    elif mtp_path and str(mtp_path).strip():
        try:
            if str(Path(mtp_path).resolve()).lower() == str(model_path.resolve()).lower():
                info['is_mtp'] = True
        except Exception:
            pass

    return info


def create_api_payload(model_id, messages, temperature=0.7, max_tokens=256):
    """创建OpenAI兼容API请求体"""
    return {
        "model": model_id,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False
    }


def estimate_vram_usage(param_count_b, quant_bits, context_size, batch_size=512):
    """
    估算显存使用 (MB)
    简化公式: 模型权重 + KV Cache
    """
    try:
        params = float(param_count_b.replace('B', '').replace('b', ''))
    except Exception:
        params = 7  # 默认7B

    weight_mb = params * quant_bits / 8 * 1024  # 权重显存
    kv_mb = context_size * batch_size * 2 * 2 / (1024**2)  # KV Cache (简化)
    overhead_mb = 500  # 额外开销
    return int(weight_mb + kv_mb + overhead_mb)


def truncate_text(text, max_len=100):
    """截断文本"""
    if len(text) <= max_len:
        return text
    return text[:max_len-3] + '...'
