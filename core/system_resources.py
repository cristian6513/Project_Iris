"""Facts about this PC that Core and the wizard both need: RAM, CPU and GPU.

Moved from wizard/system_profile.py (2026-10-21) so Core can apply the local
voice requirements (core/voice_requirements.py) without importing the wizard.
"""

from __future__ import annotations

import ctypes
import os
import platform
import subprocess
from pathlib import Path
from typing import Any


def _memory_status() -> Any:
    """Windows GlobalMemoryStatusEx, or None elsewhere or on failure."""
    if os.name != "nt":
        return None

    class _MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status
    except (AttributeError, OSError, ValueError):
        pass
    return None


def available_ram_gb() -> float | None:
    """RAM free right now (it changes from one moment to the next)."""
    try:
        import psutil  # type: ignore[import-not-found]

        return round(float(psutil.virtual_memory().available) / (1024**3), 1)
    except (ImportError, OSError, ValueError):
        pass
    status = _memory_status()
    if status is not None:
        return round(float(status.ullAvailPhys) / (1024**3), 1)
    return None


def total_ram_gb() -> float | None:
    """Return physical RAM without requiring the optional psutil extra."""
    try:
        import psutil  # type: ignore[import-not-found]

        return round(float(psutil.virtual_memory().total) / (1024**3), 1)
    except (ImportError, OSError, ValueError):
        pass

    if os.name == "nt":
        try:
            class _MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatus()
            status.dwLength = ctypes.sizeof(_MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return round(float(status.ullTotalPhys) / (1024**3), 1)
        except (AttributeError, OSError, ValueError):
            pass

    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        if page_size > 0 and pages > 0:
            return round((page_size * pages) / (1024**3), 1)
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    return None


def _command_output(argv: list[str], timeout: float = 4.0) -> str:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return ""
    return (result.stdout or "").strip() if result.returncode == 0 else ""


def cpu_profile() -> dict[str, Any]:
    logical = os.cpu_count() or 1
    physical: int | None = None
    try:
        import psutil  # type: ignore[import-not-found]

        physical = psutil.cpu_count(logical=False)
    except (ImportError, OSError):
        pass

    name = (platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "")).strip()
    if not name and platform.system() == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.lower().startswith(("model name", "hardware")) and ":" in line:
                    name = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
    if not name and platform.system() == "Darwin":
        name = _command_output(["sysctl", "-n", "machdep.cpu.brand_string"])

    return {
        "name": name or "CPU no identificada",
        "logical_cores": logical,
        "physical_cores": physical,
    }


def gpu_profile() -> dict[str, Any]:
    """Detect useful acceleration while keeping generic GPUs informational."""
    result: dict[str, Any] = {
        "detected": False,
        "name": "",
        "acceleration": "cpu",
        "vram_gb": None,
    }

    try:
        import torch  # type: ignore[import-not-found]

        if bool(torch.cuda.is_available()):
            result.update({
                "detected": True,
                "name": str(torch.cuda.get_device_name(0)),
                "acceleration": "cuda",
            })
            try:
                props = torch.cuda.get_device_properties(0)
                result["vram_gb"] = round(float(props.total_memory) / (1024**3), 1)
            except (AttributeError, RuntimeError, ValueError):
                pass
            return result
    except (ImportError, OSError, RuntimeError):
        pass

    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin" and machine in {"arm64", "aarch64"}:
        result.update({
            "detected": True,
            "name": "Apple Silicon (GPU integrada)",
            "acceleration": "metal",
        })
        return result

    nvidia = _command_output([
        "nvidia-smi",
        "--query-gpu=name,memory.total",
        "--format=csv,noheader,nounits",
    ])
    if nvidia:
        first = nvidia.splitlines()[0]
        parts = [part.strip() for part in first.rsplit(",", 1)]
        result.update({"detected": True, "name": parts[0], "acceleration": "cuda"})
        if len(parts) == 2:
            try:
                result["vram_gb"] = round(float(parts[1]) / 1024, 1)
            except ValueError:
                pass
        return result

    generic = ""
    if system == "Windows":
        generic = _command_output([
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
        ])
    elif system == "Linux":
        display_rows = _command_output(["lspci"])
        generic = "\n".join(
            line.split(": ", 1)[-1]
            for line in display_rows.splitlines()
            if any(token in line.lower() for token in ("vga compatible", "3d controller", "display controller"))
        )
    elif system == "Darwin":
        generic = _command_output(["system_profiler", "SPDisplaysDataType", "-detailLevel", "mini"])

    if generic:
        result.update({
            "detected": True,
            "name": generic.splitlines()[0].strip(),
            "acceleration": "generic",
        })
    return result
