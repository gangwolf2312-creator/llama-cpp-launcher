"""根据 GPU/系统硬件和模型类型，生成推荐启动参数
并格式化硬件摘要。
"""
from pathlib import Path
from utils.gguf_utils import gguf_context_length, gguf_model_type, gguf_has_mtp
from utils.helpers import clamp_ctx_to_gears


HARDWARE_TIERS = [
    (8192, 'low'),
    (16384, 'medium'),
    (32768, 'high'),
    (float('inf'), 'ultra'),
]

_TIER_LABELS = {
    'low': '入门（<8GB）',
    'medium': '中端（8-16GB）',
    'high': '高端（16-32GB）',
    'ultra': '旗舰（≥32GB）',
}


def get_hardware_tier(gpu_info):
    """根据 GPU 总显存（含共享内存）划分档位"""
    total_mb = gpu_info.get('primary', {}).get('total_mb', 0) if gpu_info else 0
    for limit, tier in HARDWARE_TIERS:
        if total_mb < limit:
            return tier
    return 'low'


def is_amd_igpu(gpu_info):
    """判断是否是 AMD APU / iGPU（共享大内存平台）"""
    if not gpu_info:
        return False
    primary = gpu_info.get('primary', {})
    name = (primary.get('name', '') + ' ' + primary.get('vendor', '')).lower()
    return 'amd' in name and 'radeon' in name


def get_model_type(model_path):
    """读取模型是 dense 还是 moe"""
    if not model_path or not Path(model_path).exists():
        return 'unknown'
    return gguf_model_type(model_path)


def _clamp_to_model_ctx(path, ctx):
    """把推荐上下文限制在模型真实最大上下文内"""
    try:
        model_ctx = gguf_context_length(path)
        if model_ctx and isinstance(model_ctx, int) and model_ctx > 0:
            return min(ctx, model_ctx)
    except Exception:
        pass
    return ctx


def format_hardware_summary(gpu_info, system_info):
    """把硬件信息格式化成单行字符串，用于右侧显示"""
    if not gpu_info or not gpu_info.get('primary'):
        return "未检测到 GPU"
    primary = gpu_info['primary']
    name = primary.get('name', 'Unknown')
    total_mb = primary.get('total_mb', 0)
    dedicated_mb = primary.get('dedicated_mb', 0)
    shared_mb = primary.get('shared_mb', 0)
    total_gb = total_mb / 1024
    dedicated_gb = dedicated_mb / 1024
    shared_gb = shared_mb / 1024
    ram_gb = system_info.get('memory_gb', 0) if system_info else 0
    tier = get_hardware_tier(gpu_info)
    label = _TIER_LABELS.get(tier, tier)
    return (
        f"GPU: {name} | 总显存: {total_gb:.1f}GB "
        f"(专用 {dedicated_gb:.1f}GB / 共享 {shared_gb:.1f}GB) "
        f"| 内存: {ram_gb:.1f}GB | 档位: {label}"
    )


def get_recommended_params(model_path, gpu_info, system_info):
    """
    返回推荐参数。

    返回：(
        preset_info: {'name': str, 'description': str, 'tier': str, 'model_type': str},
        params: {param_name: value}
    )
    """
    tier = get_hardware_tier(gpu_info)
    model_type = get_model_type(model_path) if model_path else 'unknown'
    vram_mb = (gpu_info.get('primary', {}).get('total_mb', 0)
               if gpu_info else 0)
    ram_gb = system_info.get('memory_gb', 16) if system_info else 16
    cpu_threads = system_info.get('cpu_threads', 8) if system_info else 8
    amd_igpu = is_amd_igpu(gpu_info)

    # 基础上下文按档位和模型类型双维度
    if tier == 'ultra':
        ctx = 131072 if model_type == 'moe' else 262144
    elif tier == 'high':
        ctx = 65536 if model_type == 'moe' else 131072
    elif tier == 'medium':
        ctx = 16384 if model_type == 'moe' else 32768
    else:
        ctx = 4096 if model_type == 'moe' else 8192
    ctx = _clamp_to_model_ctx(model_path, ctx)

    # 线程数：偶数，最多 32
    threads = max(2, (cpu_threads // 2) * 2)
    threads = min(threads, 32)

    # 批大小、并发槽
    if tier == 'ultra':
        batch = 4096
        ubatch = 512
        parallel = 4
    elif tier == 'high':
        batch = 2048
        ubatch = 512
        parallel = 4
    elif tier == 'medium':
        batch = 1024
        ubatch = 256
        parallel = 2
    else:
        batch = 512
        ubatch = 128
        parallel = 1

    # 检测是否内置 MTP 层
    has_mtp = False
    try:
        has_mtp = bool(model_path) and gguf_has_mtp(model_path)
    except Exception:
        pass

    # AMD APU / iGPU 且共享大内存时开启统一 KV、FlashAttn。
    # 注意：-kvu 与 -fit 不建议同时开启，因此此处不自动启用 -fit。
    kv_unified = amd_igpu
    flash_attn = 'on' if amd_igpu else 'auto'
    cont_batching = True
    fit = False
    fit_ctx = min(8192, ctx)

    # KV cache 量化：AMD 共享大内存平台用 q8_0 + turbo4 节省带宽
    cache_type_k = 'q8_0' if amd_igpu else 'f16'
    cache_type_v = 'turbo4' if amd_igpu else 'f16'

    # 保守保护：显存/内存不足时降级
    if vram_mb < 6144 and parallel > 1:
        parallel = 1
    if ram_gb < 16 and ctx > 65536:
        ctx = 65536
        ctx = _clamp_to_model_ctx(model_path, ctx)
    # AMD APU / MoE 模型更适合保守并发，避免共享内存带宽瓶颈
    if amd_igpu or model_type == 'moe':
        parallel = 1

    # 最终把上下文对齐到 UI 允许的档位（32k/64k/128k/256k）
    ctx = clamp_ctx_to_gears(ctx)

    params = {
        'ctx-size': ctx,
        'ngl': 99,
        'parallel': parallel,
        'batch-size': batch,
        'ubatch-size': ubatch,
        'threads': threads,
        'threads-http': 2,
        'cont-batching': cont_batching,
        'flash-attn': flash_attn,
        'kv-unified': kv_unified,
        'no-kv-unified': False,
        'fit': fit,
        'cache-type-k': cache_type_k,
        'cache-type-v': cache_type_v,
    }
    if fit:
        params['fit-ctx'] = fit_ctx
    else:
        # 显式关闭 fit-ctx，避免历史配置残留导致命令行只带 -fitc 不带 -fit
        params['fit-ctx'] = False

    # 内置 MTP 层的模型：推荐用 draft-mtp 模式利用自身 MTP 层
    if has_mtp:
        params['spec-type'] = 'draft-mtp'
        params['spec-draft-n-max'] = 3

    model_type_label = model_type.upper()
    if has_mtp:
        model_type_label += '+MTP'

    preset_info = {
        'name': _TIER_LABELS.get(tier, tier),
        'description': (
            f"检测为 {model_type_label} 模型，"
            f"推荐 {ctx} 上下文，并行槽={parallel}，"
            f"{'已针对 AMD APU 开启 -kvu / flash-attn' if amd_igpu else '按常规 GPU 配置'}"
            f"{'，已启用内置 MTP (draft-mtp)' if has_mtp else ''}"
        ),
        'tier': tier,
        'model_type': model_type,
        'has_mtp': has_mtp,
        'vram_mb': vram_mb,
        'ram_gb': ram_gb,
    }
    return preset_info, params
