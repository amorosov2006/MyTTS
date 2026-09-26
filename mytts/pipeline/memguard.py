"""Real macOS memory introspection, library form of bench/memguard.py.

Used by pipeline.worker.ProcessWorker's supervisor to enforce PLAN.md's "Memory safety" rule:
never trust mx.get_peak_memory(); measure the real phys_footprint and system availability.
All calls are direct kernel queries (no subprocesses): cheap enough for the event loop and
they keep working under memory pressure, when forking vm_stat could fail.
"""
from __future__ import annotations

import ctypes
import ctypes.util

_libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
_libc = ctypes.CDLL(ctypes.util.find_library("c"))
_RUSAGE_INFO_V2 = 2
_PHYS_FOOTPRINT_IDX = 9  # uint64 index in rusage_info_v2 (uuid occupies 0-1)
_HOST_VM_INFO64 = 4


class _VMStatistics64(ctypes.Structure):  # <mach/vm_statistics.h> vm_statistics64
    _fields_ = [(n, ctypes.c_uint32) for n in ("free", "active", "inactive", "wire")] + \
               [(n, ctypes.c_uint64) for n in ("zero_fill", "reactivations", "pageins", "pageouts",
                                                "faults", "cow_faults", "lookups", "hits", "purges")] + \
               [(n, ctypes.c_uint32) for n in ("purgeable", "speculative")] + \
               [(n, ctypes.c_uint64) for n in ("decompressions", "compressions", "swapins", "swapouts")] + \
               [(n, ctypes.c_uint32) for n in ("compressor", "throttled", "external", "internal")] + \
               [("total_uncompressed_in_compressor", ctypes.c_uint64)]


_libc.mach_host_self.restype = ctypes.c_uint32
_HOST = _libc.mach_host_self()
_PAGE = ctypes.c_size_t.in_dll(_libc, "vm_kernel_page_size").value


def footprint(pid: int) -> int:
    """Real phys_footprint (bytes) of one process, as Activity Monitor shows it (includes
    Metal/GPU buffers). 0 if the pid is gone or inaccessible."""
    buf = (ctypes.c_uint64 * 32)()
    if _libproc.proc_pid_rusage(pid, _RUSAGE_INFO_V2, ctypes.byref(buf)) != 0:
        return 0
    return buf[_PHYS_FOOTPRINT_IDX]


def available_bytes() -> int:
    """System-wide memory available without swapping: free + inactive + speculative + purgeable
    pages (same definition as `vm_stat`-based bench/memguard.py)."""
    st = _VMStatistics64()
    count = ctypes.c_uint32(ctypes.sizeof(st) // 4)
    if _libc.host_statistics64(_HOST, _HOST_VM_INFO64, ctypes.byref(st), ctypes.byref(count)) != 0:
        raise OSError("host_statistics64 failed")
    return _PAGE * (st.free + st.inactive + st.speculative + st.purgeable)


def total_bytes() -> int:
    """Total physical memory installed (sysctl hw.memsize)."""
    size = ctypes.c_uint64()
    n = ctypes.c_size_t(8)
    _libc.sysctlbyname(b"hw.memsize", ctypes.byref(size), ctypes.byref(n), None, 0)
    return size.value
