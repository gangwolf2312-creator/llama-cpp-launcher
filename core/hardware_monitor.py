"""
Hardware Monitor - CPU/Memory/GPU real-time monitoring
Optimized for AMD Ryzen AI MAX+ 395 / Radeon 8060S iGPU with dedicated VRAM allocation
Uses multiple precision methods: DXGI (most accurate) -> Registry -> WMI -> PDH
"""
import threading
import time
import platform
import re
import ctypes
from ctypes import wintypes
from ctypes import HRESULT, POINTER, c_uint, c_ulonglong, c_wchar, c_size_t
import struct


# ============================================================================
# DXGI Constants and Structures for ctypes
# ============================================================================
class LUID(ctypes.Structure):
    _fields_ = [
        ("LowPart", wintypes.DWORD),
        ("HighPart", wintypes.LONG),
    ]


class DXGI_ADAPTER_DESC(ctypes.Structure):
    _fields_ = [
        ("Description", c_wchar * 128),
        ("VendorId", c_uint),
        ("DeviceId", c_uint),
        ("SubSysId", c_uint),
        ("Revision", c_uint),
        ("DedicatedVideoMemory", c_size_t),
        ("DedicatedSystemMemory", c_size_t),
        ("SharedSystemMemory", c_size_t),
        ("AdapterLuid", LUID),
    ]


# IID for IDXGIFactory = {7b7166ec-21c7-44ae-b21a-c9ae321ae369}
IID_IDXGIFactory = ctypes.c_buffer(
    bytes([0xec, 0x66, 0x71, 0x7b, 0xc7, 0x21, 0xae, 0x44,
           0xb2, 0x1a, 0xc9, 0xae, 0x32, 0x1a, 0xe3, 0x69])
)

# DXGI Error
DXGI_ERROR_NOT_FOUND = 0x887A0002


class DXGI_GPUReader:
    """GPU info reader using Windows DXGI API - most accurate method"""

    def __init__(self):
        self._available = False
        self._factory = None
        self._init_dxgi()

    def _init_dxgi(self):
        """Initialize DXGI factory via ctypes"""
        try:
            dxgi = ctypes.windll.dxgi
            self._create_dxgi_factory = dxgi.CreateDXGIFactory
            self._create_dxgi_factory.argtypes = [
                ctypes.c_void_p,  # REFIID
                ctypes.POINTER(ctypes.c_void_p)  # ppFactory
            ]
            self._create_dxgi_factory.restype = HRESULT

            # Create factory
            factory_ptr = ctypes.c_void_p()
            hr = self._create_dxgi_factory(
                ctypes.byref(IID_IDXGIFactory),
                ctypes.byref(factory_ptr)
            )
            if hr == 0 and factory_ptr.value:
                self._factory = factory_ptr
                self._available = True
        except Exception:
            self._available = False

    def is_available(self):
        return self._available

    def enum_adapters(self):
        """Enumerate all GPU adapters, return list of dicts"""
        adapters = []
        if not self._available or not self._factory:
            return adapters

        try:
            # Get vtable
            vtable = ctypes.cast(
                ctypes.c_void_p(self._factory.value),
                ctypes.POINTER(ctypes.c_void_p)
            ).contents.value
            vtable_ptr = ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))

            # EnumAdapters is at index 7 in vtable
            enum_adapters_fn = ctypes.CFUNCTYPE(
                HRESULT,
                ctypes.c_void_p, c_uint, ctypes.POINTER(ctypes.c_void_p)
            )(vtable_ptr[7])

            # GetDesc is at index 8 in IDXGIAdapter vtable
            i = 0
            while True:
                adapter_ptr = ctypes.c_void_p()
                hr = enum_adapters_fn(self._factory, i, ctypes.byref(adapter_ptr))
                if hr != 0 or not adapter_ptr.value:
                    break

                # Get vtable for adapter
                adapter_vtable = ctypes.cast(
                    ctypes.c_void_p(adapter_ptr.value),
                    ctypes.POINTER(ctypes.c_void_p)
                ).contents.value
                adapter_vtable_ptr = ctypes.cast(
                    adapter_vtable, ctypes.POINTER(ctypes.c_void_p)
                )

                get_desc_fn = ctypes.CFUNCTYPE(
                    HRESULT,
                    ctypes.c_void_p, ctypes.POINTER(DXGI_ADAPTER_DESC)
                )(adapter_vtable_ptr[8])

                desc = DXGI_ADAPTER_DESC()
                hr = get_desc_fn(adapter_ptr, ctypes.byref(desc))
                if hr == 0:
                    # Vendor ID mapping
                    vendor = self._vendor_name(desc.VendorId)
                    adapters.append({
                        "index": i,
                        "name": desc.Description,
                        "vendor_id": desc.VendorId,
                        "vendor": vendor,
                        "device_id": desc.DeviceId,
                        "dedicated_video_mb": desc.DedicatedVideoMemory // (1024 * 1024),
                        "dedicated_system_mb": desc.DedicatedSystemMemory // (1024 * 1024),
                        "shared_system_mb": desc.SharedSystemMemory // (1024 * 1024),
                        "total_available_mb": (
                            desc.DedicatedVideoMemory +
                            desc.DedicatedSystemMemory +
                            desc.SharedSystemMemory
                        ) // (1024 * 1024),
                        "is_software": desc.VendorId == 0x1414,  # Microsoft Basic Render
                        "luid": f"luid_0x{desc.AdapterLuid.HighPart:08x}_0x{desc.AdapterLuid.LowPart:08x}",
                        "luid_high": desc.AdapterLuid.HighPart,
                        "luid_low": desc.AdapterLuid.LowPart,
                    })

                # Release adapter (Release is index 2)
                release_fn = ctypes.CFUNCTYPE(
                    c_uint, ctypes.c_void_p
                )(adapter_vtable_ptr[2])
                release_fn(adapter_ptr)
                i += 1

        except Exception:
            pass

        return adapters

    def get_amd_igpu_vram(self):
        """Get AMD iGPU dedicated VRAM (for AMD Adrenalin allocated memory)"""
        adapters = self.enum_adapters()
        for a in adapters:
            if a["vendor"] == "AMD" and not a["is_software"]:
                # DedicatedVideoMemory = what AMD Adrenalin assigned
                if a["dedicated_video_mb"] > 0:
                    return a["dedicated_video_mb"], a["shared_system_mb"], a["name"]
        return 0, 0, ""

    def get_all_gpu_info(self):
        """Get all GPU info as dict"""
        adapters = self.enum_adapters()
        return {
            "adapters": adapters,
            "primary": adapters[0] if adapters else None,
            "count": len(adapters),
        }

    @staticmethod
    def _vendor_name(vendor_id):
        vendors = {
            0x1002: "AMD",
            0x1022: "AMD",
            0x10DE: "NVIDIA",
            0x8086: "Intel",
            0x1414: "Microsoft",
        }
        return vendors.get(vendor_id, f"Unknown(0x{vendor_id:04X})")

    def __del__(self):
        if self._factory:
            try:
                vtable = ctypes.cast(
                    ctypes.c_void_p(self._factory.value),
                    ctypes.POINTER(ctypes.c_void_p)
                ).contents.value
                vtable_ptr = ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))
                release_fn = ctypes.CFUNCTYPE(
                    c_uint, ctypes.c_void_p
                )(vtable_ptr[2])
                release_fn(self._factory)
            except Exception:
                pass


# ============================================================================
# Registry reader for AMD GPU VRAM
# ============================================================================
class AMDRegistryReader:
    """Read AMD GPU VRAM from Windows Registry"""

    AMD_GPU_CLASS_KEY = (
        r"SYSTEM\CurrentControlSet\Control\Class"
        r"\{4d36e968-e325-11ce-bfc1-08002be10318}"
    )

    @staticmethod
    def read_vram_mb():
        """Read dedicated VRAM from registry in MB"""
        if platform.system() != "Windows":
            return 0

        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                AMDRegistryReader.AMD_GPU_CLASS_KEY,
            ) as key:
                idx = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(key, idx)
                        idx += 1
                        try:
                            with winreg.OpenKey(key, subkey_name) as subkey:
                                # Check if AMD
                                try:
                                    desc, _ = winreg.QueryValueEx(
                                        subkey, "DriverDesc"
                                    )
                                    if not AMDRegistryReader._is_amd(str(desc)):
                                        continue
                                except FileNotFoundError:
                                    continue

                                # Read qwMemorySize (64-bit, most accurate)
                                try:
                                    mem_size, _ = winreg.QueryValueEx(
                                        subkey,
                                        "HardwareInformation.qwMemorySize",
                                    )
                                    if mem_size and mem_size > 0:
                                        return mem_size // (1024 * 1024)
                                except FileNotFoundError:
                                    pass

                                # Fallback: Read DedicatedSegmentSize
                                try:
                                    seg_size, _ = winreg.QueryValueEx(
                                        subkey,
                                        "HardwareInformation.DedicatedSegmentSize",
                                    )
                                    if seg_size and seg_size > 0:
                                        return seg_size // 1024
                                except FileNotFoundError:
                                    pass

                        except Exception:
                            continue
                    except OSError:
                        break
        except Exception:
            pass

        return 0

    @staticmethod
    def read_amd_adrenalin_vram():
        """
        Read AMD Adrenalin configured dedicated graphics memory.
        Returns (dedicated_mb, shared_mb, gpu_name) or (0, 0, "")
        """
        if platform.system() != "Windows":
            return 0, 0, ""

        try:
            import winreg
            dedicated_mb = 0
            gpu_name = ""

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                AMDRegistryReader.AMD_GPU_CLASS_KEY,
            ) as key:
                idx = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(key, idx)
                        idx += 1
                        try:
                            with winreg.OpenKey(key, subkey_name) as subkey:
                                # Get GPU name
                                try:
                                    desc, _ = winreg.QueryValueEx(
                                        subkey, "DriverDesc"
                                    )
                                    desc_str = str(desc)
                                    if not AMDRegistryReader._is_amd(desc_str):
                                        continue
                                    gpu_name = desc_str
                                except FileNotFoundError:
                                    continue

                                # Primary: qwMemorySize (64-bit)
                                try:
                                    mem_size, _ = winreg.QueryValueEx(
                                        subkey,
                                        "HardwareInformation.qwMemorySize",
                                    )
                                    if mem_size and mem_size > 0:
                                        dedicated_mb = mem_size // (1024 * 1024)
                                except FileNotFoundError:
                                    pass

                                if dedicated_mb == 0:
                                    # Fallback: DedicatedSegmentSize
                                    try:
                                        seg, _ = winreg.QueryValueEx(
                                            subkey,
                                            "HardwareInformation."
                                            "DedicatedSegmentSize",
                                        )
                                        if seg and seg > 0:
                                            dedicated_mb = seg // 1024
                                    except FileNotFoundError:
                                        pass

                                if dedicated_mb == 0:
                                    # Fallback 2: AdapterRAM (32-bit, may cap at 4GB)
                                    try:
                                        ram, _ = winreg.QueryValueEx(
                                            subkey, "HardwareInformation."
                                            "AdapterString"
                                        )
                                        # Not directly VRAM, just used for detection
                                    except FileNotFoundError:
                                        pass

                        except Exception:
                            continue
                    except OSError:
                        break

            # Estimate shared memory if we have dedicated
            if dedicated_mb > 0:
                return dedicated_mb, 0, gpu_name

        except Exception:
            pass

        return 0, 0, ""

    @staticmethod
    def _is_amd(name):
        """Check if GPU name indicates AMD"""
        upper = name.upper()
        return "AMD" in upper or "RADEON" in upper or "ATI" in upper


# ============================================================================
# Main Hardware Monitor
# ============================================================================
class HardwareMonitor:
    """Hardware monitor - CPU/Memory/GPU real-time data"""

    def __init__(self, interval=1.0):
        self.interval = interval
        self._running = False
        self._thread = None
        self._callbacks = []
        self._callbacks_lock = threading.Lock()
        self._data = {
            "cpu_percent": 0.0,
            "memory_used_gb": 0.0,
            "memory_total_gb": 0.0,
            "memory_percent": 0.0,
            "gpu_percent": 0.0,
            "gpu_vram_used_mb": 0.0,
            "gpu_vram_total_mb": 0.0,
            "gpu_vram_shared_mb": 0.0,
            "gpu_engine": "",
            "gpu_name": "",
        }
        self._psutil_available = False
        self._wmi_available = False
        self._winperf_available = False
        self._dxgi = None

        # PDH query/counter handles for GPU monitoring
        self._gpu_luid = None
        self._gpu_util_query = None
        self._gpu_util_counter = None
        self._gpu_util_ready = False
        self._gpu_mem_query = None
        self._gpu_mem_counter = None
        self._gpu_mem_ready = False

        self._init_backends()

    def _init_backends(self):
        """Initialize monitoring backends"""
        try:
            import psutil
            self._psutil_available = True
            self._data["memory_total_gb"] = (
                psutil.virtual_memory().total / (1024 ** 3)
            )
        except ImportError:
            pass

        if platform.system() == "Windows":
            try:
                import win32pdh
                self._winperf_available = True
            except ImportError:
                pass

            try:
                import wmi
                self._c = wmi.WMI()
                self._wmi_available = True
            except ImportError:
                pass

            # Initialize DXGI (most accurate for GPU VRAM)
            self._dxgi = DXGI_GPUReader()

            # Cache primary GPU LUID and open persistent PDH queries
            self._gpu_luid = self._detect_primary_gpu_luid()
            self._open_pdh_queries()

    def _detect_primary_gpu_luid(self):
        """Return the LUID string of the primary (first non-software) GPU."""
        if self._dxgi and self._dxgi.is_available():
            try:
                for a in self._dxgi.enum_adapters():
                    if a.get("is_software"):
                        continue
                    return a.get("luid", "").lower()
            except Exception:
                pass
        return None

    def _open_pdh_queries(self):
        """Open persistent PDH queries for GPU utilization and memory."""
        if not self._winperf_available:
            return
        try:
            import win32pdh
            self._gpu_util_query = win32pdh.OpenQuery()
            self._gpu_util_counter = win32pdh.AddCounter(
                self._gpu_util_query,
                r"\GPU Engine(*)\Utilization Percentage",
            )

            self._gpu_mem_query = win32pdh.OpenQuery()
            self._gpu_mem_counter = win32pdh.AddCounter(
                self._gpu_mem_query,
                r"\GPU Adapter Memory(*)\Total Committed",
            )
        except Exception:
            self._gpu_util_query = None
            self._gpu_util_counter = None
            self._gpu_mem_query = None
            self._gpu_mem_counter = None

    def _close_pdh_queries(self):
        """Close persistent PDH queries."""
        if self._winperf_available:
            try:
                import win32pdh
                for q in (self._gpu_util_query, self._gpu_mem_query):
                    if q:
                        try:
                            win32pdh.CloseQuery(q)
                        except Exception:
                            pass
            except Exception:
                pass
        self._gpu_util_query = None
        self._gpu_util_counter = None
        self._gpu_mem_query = None
        self._gpu_mem_counter = None
        self._gpu_util_ready = False
        self._gpu_mem_ready = False

    def _read_cpu(self):
        if self._psutil_available:
            try:
                import psutil
                return psutil.cpu_percent(interval=None)
            except Exception:
                pass
        return 0.0

    def _read_memory(self):
        if self._psutil_available:
            try:
                import psutil
                mem = psutil.virtual_memory()
                return (
                    mem.used / (1024 ** 3),
                    mem.total / (1024 ** 3),
                    mem.percent,
                )
            except Exception:
                pass
        return 0.0, self._data["memory_total_gb"], 0.0

    def _read_gpu(self):
        """Read GPU info using best available method"""
        gpu_util = 0.0
        vram_used = 0.0
        vram_total = 0.0
        vram_shared = 0.0
        gpu_name = ""
        engine = ""

        # Method 1: DXGI (most accurate for AMD Adrenalin allocated VRAM)
        if self._dxgi and self._dxgi.is_available():
            try:
                adapters = self._dxgi.enum_adapters()
                for a in adapters:
                    if a["is_software"]:
                        continue
                    gpu_name = a["name"]
                    vram_total = a["dedicated_video_mb"]
                    vram_shared = a["shared_system_mb"]
                    engine = a["vendor"]
                    break  # Use first non-software GPU
            except Exception:
                pass

        # Method 2: Registry (AMD-specific, for iGPU dedicated VRAM)
        if vram_total == 0 and platform.system() == "Windows":
            try:
                dedicated, shared, name = (
                    AMDRegistryReader.read_amd_adrenalin_vram()
                )
                if dedicated > 0:
                    vram_total = dedicated
                    vram_shared = shared
                    if name:
                        gpu_name = name
                    engine = "AMD"
            except Exception:
                pass

        # Method 3: WMI (least accurate, uint32 caps at 4GB)
        if vram_total == 0 and self._wmi_available:
            try:
                for gpu in self._c.Win32_VideoController():
                    if gpu.AdapterRAM and gpu.AdapterRAM > 0:
                        # WMI AdapterRAM is uint32, caps at ~4GB
                        vram_total = gpu.AdapterRAM // (1024 ** 2)
                        gpu_name = gpu.Name or ""
                        break
            except Exception:
                pass

        # GPU utilization via Performance Counters
        if self._winperf_available:
            try:
                import win32pdh
                gpu_util = self._read_gpu_util_pdh(win32pdh)
            except Exception:
                pass

        # GPU memory usage via Performance Counters
        if self._winperf_available:
            try:
                import win32pdh
                used = self._read_gpu_vram_usage_pdh(win32pdh)
                if used > 0:
                    vram_used = used
            except Exception:
                pass

        # Detect GPU type if not already set
        if not engine:
            engine = self._detect_gpu_type()

        # Mark llama process activity
        if self._psutil_available and gpu_util == 0:
            try:
                import psutil
                for proc in psutil.process_iter(["pid", "name"]):
                    if proc.info["name"] and "llama" in proc.info["name"].lower():
                        engine = f"{engine}-llama".strip("-") if engine else "llama"
                        break
            except Exception:
                pass

        return gpu_util, vram_used, vram_total, vram_shared, gpu_name, engine

    def _read_gpu_util_pdh(self, win32pdh):
        """Read GPU utilization via PDH (persistent query, LUID-aware)."""
        if not self._gpu_util_query or not self._gpu_util_counter:
            return 0.0

        try:
            win32pdh.CollectQueryData(self._gpu_util_query)
            if not self._gpu_util_ready:
                # First sample only establishes the baseline; return on the
                # next poll.
                self._gpu_util_ready = True
                return 0.0

            items = win32pdh.GetFormattedCounterArray(
                self._gpu_util_counter, win32pdh.PDH_FMT_DOUBLE
            )
        except Exception:
            return 0.0

        luid = self._gpu_luid
        total = 0.0

        # Instance names look like:
        # pid_<pid>_luid_0x00000000_0x0001426f_phys_0_eng_0_engtype_3D
        _LUID_RE = re.compile(r"luid_(0x[0-9a-f]+_0x[0-9a-f]+)", re.IGNORECASE)

        for instance_name, value in items.items():
            inst_lower = instance_name.lower()
            if luid:
                m = _LUID_RE.search(inst_lower)
                if not m or f"luid_{m.group(1).lower()}" != luid:
                    continue
                total += value
            elif "engtype_3d" in inst_lower or "engtype_graphics" in inst_lower:
                total += value

        # Cap at 100%; the counter can momentarily round above it.
        return round(min(total, 100.0), 1)

    def _read_gpu_vram_usage_pdh(self, win32pdh):
        """Read GPU memory usage via PDH (Total Committed, LUID-aware)."""
        if not self._gpu_mem_query or not self._gpu_mem_counter:
            return 0.0

        try:
            win32pdh.CollectQueryData(self._gpu_mem_query)
            if not self._gpu_mem_ready:
                self._gpu_mem_ready = True
                return 0.0

            items = win32pdh.GetFormattedCounterArray(
                self._gpu_mem_counter, win32pdh.PDH_FMT_DOUBLE
            )
        except Exception:
            return 0.0

        luid = self._gpu_luid
        total = 0.0

        for instance_name, value in items.items():
            if luid:
                if luid not in instance_name.lower():
                    continue
            total += value

        # Total Committed is in bytes; convert to MB.
        return total / (1024 ** 2)

    def _detect_gpu_type(self):
        """Detect GPU vendor"""
        if platform.system() != "Windows":
            return ""
        try:
            import wmi
            c = wmi.WMI()
            for gpu in c.Win32_VideoController():
                name = gpu.Name or ""
                if "AMD" in name or "Radeon" in name:
                    return "AMD"
                elif "NVIDIA" in name or "GeForce" in name:
                    return "NVIDIA"
                elif "Intel" in name:
                    return "Intel"
        except Exception:
            pass
        return ""

    def _monitor_loop(self):
        while self._running:
            self._data["cpu_percent"] = self._read_cpu()
            mem_used, mem_total, mem_pct = self._read_memory()
            self._data["memory_used_gb"] = mem_used
            self._data["memory_total_gb"] = mem_total
            self._data["memory_percent"] = mem_pct

            (
                gpu_pct,
                vram_used,
                vram_total,
                vram_shared,
                gpu_name,
                engine,
            ) = self._read_gpu()
            self._data["gpu_percent"] = gpu_pct
            self._data["gpu_vram_used_mb"] = vram_used
            self._data["gpu_vram_total_mb"] = vram_total
            self._data["gpu_vram_shared_mb"] = vram_shared
            self._data["gpu_engine"] = engine
            self._data["gpu_name"] = gpu_name

            with self._callbacks_lock:
                callbacks = list(self._callbacks)
            for cb in callbacks:
                try:
                    cb(self._data)
                except Exception:
                    pass

            time.sleep(self.interval)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        self._close_pdh_queries()

    def add_callback(self, callback):
        with self._callbacks_lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

    def remove_callback(self, callback):
        with self._callbacks_lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def get_data(self):
        return dict(self._data)

    @staticmethod
    def get_gpu_info_static():
        """Static GPU info with full DXGI accuracy"""
        info = {"adapters": [], "primary": None, "dxgi_available": False}

        if platform.system() != "Windows":
            return info

        # Try DXGI first
        dxgi = DXGI_GPUReader()
        if dxgi.is_available():
            try:
                adapters = dxgi.enum_adapters()
                for a in adapters:
                    info["adapters"].append({
                        "index": a["index"],
                        "name": a["name"],
                        "vendor": a["vendor"],
                        "dedicated_mb": a["dedicated_video_mb"],
                        "shared_mb": a["shared_system_mb"],
                        "total_mb": a["total_available_mb"],
                    })
                info["dxgi_available"] = True
                if info["adapters"]:
                    info["primary"] = info["adapters"][0]
                return info
            except Exception:
                pass

        # Fallback to WMI
        try:
            import wmi
            c = wmi.WMI()
            for i, gpu in enumerate(c.Win32_VideoController()):
                vram_mb = 0
                if gpu.AdapterRAM and gpu.AdapterRAM > 0:
                    vram_mb = gpu.AdapterRAM // (1024 ** 2)

                # Try registry for more accurate value
                reg_vram = AMDRegistryReader.read_vram_mb()
                if reg_vram > 0 and (vram_mb == 0 or vram_mb > 4000):
                    vram_mb = reg_vram

                info["adapters"].append({
                    "index": i,
                    "name": gpu.Name or "Unknown",
                    "vendor": "",
                    "dedicated_mb": vram_mb,
                    "shared_mb": 0,
                    "total_mb": vram_mb,
                })
            if info["adapters"]:
                info["primary"] = info["adapters"][0]
        except Exception:
            pass

        return info

    @staticmethod
    def get_system_info():
        import platform
        try:
            import psutil
            return {
                "cpu": platform.processor(),
                "cpu_cores": psutil.cpu_count(logical=False),
                "cpu_threads": psutil.cpu_count(logical=True),
                "memory_gb": psutil.virtual_memory().total / (1024 ** 3),
                "os": f"{platform.system()} {platform.release()}",
                "python": platform.version(),
            }
        except ImportError:
            return {
                "cpu": platform.processor(),
                "os": f"{platform.system()} {platform.release()}",
            }
