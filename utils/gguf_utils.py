"""GGUF 元数据读取工具
- 上下文长度
- 模型架构 / 类型（稠密 vs MoE）
- 是否内置 MTP (Multi-Token Prediction) 层
"""
import struct
from pathlib import Path


def _read_string(data, offset):
    """从 offset 读取一个 uint64 长度前缀的 UTF-8 字符串"""
    if offset + 8 > len(data):
        return None, len(data)
    strlen = struct.unpack('<Q', data[offset:offset + 8])[0]
    if offset + 8 + strlen > len(data):
        return None, len(data)
    s = data[offset + 8:offset + 8 + strlen]
    return s.decode('utf-8', errors='ignore'), offset + 8 + strlen


def _decode_value(vtype, data, offset):
    """根据 GGUF value type 读取单个值，返回 (value, new_offset)"""
    if offset >= len(data):
        return None, len(data)

    if vtype == 0:   # uint8
        return data[offset], offset + 1
    if vtype == 1:   # int8
        return struct.unpack('<b', data[offset:offset + 1])[0], offset + 1
    if vtype == 2:   # uint16
        return struct.unpack('<H', data[offset:offset + 2])[0], offset + 2
    if vtype == 3:   # int16
        return struct.unpack('<h', data[offset:offset + 2])[0], offset + 2
    if vtype == 4:   # uint32
        return struct.unpack('<I', data[offset:offset + 4])[0], offset + 4
    if vtype == 5:   # int32
        return struct.unpack('<i', data[offset:offset + 4])[0], offset + 4
    if vtype == 6:   # float32
        return struct.unpack('<f', data[offset:offset + 4])[0], offset + 4
    if vtype == 7:   # bool
        return bool(data[offset]), offset + 1
    if vtype == 8:   # string
        return _read_string(data, offset)
    if vtype == 9:   # array
        if offset + 12 > len(data):
            return None, len(data)
        elem_type = struct.unpack('<I', data[offset:offset + 4])[0]
        count = struct.unpack('<Q', data[offset + 4:offset + 12])[0]
        offset += 12
        arr = []
        for _ in range(count):
            val, offset = _decode_value(elem_type, data, offset)
            arr.append(val)
            if offset >= len(data):
                break
        return arr, offset
    if vtype == 10:  # uint64
        return struct.unpack('<Q', data[offset:offset + 8])[0], offset + 8
    if vtype == 11:  # int64
        return struct.unpack('<q', data[offset:offset + 8])[0], offset + 8
    if vtype == 12:  # float64
        return struct.unpack('<d', data[offset:offset + 8])[0], offset + 8

    return None, offset + 4


def gguf_metadata(path, keys=None, max_read=1024 * 1024):
    """
    读取 GGUF 文件头的 KV 元数据。
    keys: 只返回这些 key 的字典；None 则返回全部扫描到的 key。
    返回 dict[str, value]
    """
    path = Path(path)
    if not path.exists():
        return {}
    try:
        with open(path, 'rb') as f:
            data = f.read(max_read)
    except Exception:
        return {}

    if len(data) < 24:
        return {}

    magic = struct.unpack('<I', data[:4])[0]
    if magic != 0x46554747:  # 'GGUF'
        return {}

    version = struct.unpack('<I', data[4:8])[0]
    if version == 2:
        if len(data) < 16:
            return {}
        tensor_count, kv_count = struct.unpack('<II', data[8:16])
        offset = 16
    elif version == 3:
        tensor_count, kv_count = struct.unpack('<QQ', data[8:24])
        offset = 24
    else:
        return {}

    result = {}
    for _ in range(kv_count):
        if offset + 8 > len(data):
            break
        key_len = struct.unpack('<Q', data[offset:offset + 8])[0]
        offset += 8
        if offset + key_len > len(data):
            break
        key = data[offset:offset + key_len].decode('utf-8', errors='ignore')
        offset += key_len
        if offset + 4 > len(data):
            break
        vtype = struct.unpack('<I', data[offset:offset + 4])[0]
        offset += 4
        val, offset = _decode_value(vtype, data, offset)
        if keys is None or key in keys:
            result[key] = val
    return result


def gguf_context_length(path, max_read=1024 * 1024):
    """从 GGUF 元数据读取最大上下文长度"""
    meta = gguf_metadata(path, max_read=max_read)
    # 优先读取精确 key
    for key in ('llama.context_length', 'context_length'):
        if key in meta:
            return meta[key]
    # 其次读取 <arch>.context_length
    for key, val in meta.items():
        if key.endswith('.context_length'):
            return val
    return None


def gguf_model_type(path, max_read=1024 * 1024):
    """
    判断模型是稠密（dense）还是 MoE。
    依据：
      1. 是否存在 expert_count / moe_expert_count > 0 的元数据
      2. 架构名是否包含 moe / mixtral / deepseek
    """
    meta = gguf_metadata(path, max_read=max_read)
    arch = str(meta.get('general.architecture', '')).lower()

    # 检查 MoE 相关计数器
    moe_keys = [k for k in meta if 'expert_count' in k or 'moe_expert' in k]
    for k in moe_keys:
        try:
            if int(meta[k]) > 0:
                return 'moe'
        except Exception:
            pass

    # 根据架构名兜底
    if any(x in arch for x in ('moe', 'mixtral', 'deepseek')):
        return 'moe'
    return 'dense'


def gguf_has_mtp(path, max_read=1024 * 1024):
    """
    判断模型是否内置 MTP (Multi-Token Prediction) 层。
    GGUF 中对应 key: <arch>.nextn_predict_layers
    """
    meta = gguf_metadata(path, max_read=max_read)
    for key, val in meta.items():
        if key.endswith('.nextn_predict_layers'):
            try:
                return int(val) > 0
            except Exception:
                return False
    return False
