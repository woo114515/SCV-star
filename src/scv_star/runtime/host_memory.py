"""Read Windows commit headroom separately from immediately available RAM."""

import ctypes
from ctypes import wintypes
import os


class PerformanceInformation(ctypes.Structure):
    _fields_ = (
        [("cb", wintypes.DWORD)]
        + [
            (name, ctypes.c_size_t)
            for name in (
                "CommitTotal",
                "CommitLimit",
                "CommitPeak",
                "PhysicalTotal",
                "PhysicalAvailable",
                "SystemCache",
                "KernelTotal",
                "KernelPaged",
                "KernelNonpaged",
                "PageSize",
            )
        ]
        + [(name, wintypes.DWORD) for name in ("HandleCount", "ProcessCount", "ThreadCount")]
    )


def windows_memory_info():
    """GetPerformanceInfo reports page counts; return bytes, or None off Windows."""
    if os.name != "nt":
        return None
    get_info = ctypes.WinDLL("psapi", use_last_error=True).GetPerformanceInfo
    get_info.argtypes = [ctypes.POINTER(PerformanceInformation), wintypes.DWORD]
    get_info.restype = wintypes.BOOL
    info = PerformanceInformation()
    info.cb = ctypes.sizeof(info)
    if not get_info(ctypes.byref(info), info.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    if not info.PageSize or info.CommitTotal > info.CommitLimit:
        raise RuntimeError("Invalid Windows memory counters")
    return {
        "commit_used_bytes": info.CommitTotal * info.PageSize,
        "commit_limit_bytes": info.CommitLimit * info.PageSize,
        "commit_available_bytes": (info.CommitLimit - info.CommitTotal) * info.PageSize,
        "physical_available_bytes": info.PhysicalAvailable * info.PageSize,
    }
