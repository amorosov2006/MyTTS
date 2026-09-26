"""Real macOS memory introspection, library form of bench/memguard.py.

Used by pipeline.worker.ProcessWorker's supervisor to enforce PLAN.md's "Memory safety" rule:
never trust mx.get_peak_memory(); measure the real phys_footprint and system availability.
"""
from __future__ import annotations

import ctypes
import re
import subprocess

_libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
_RUSAGE_INFO_V2 = 2
_PHYS_FOOTPRINT_IDX = 9  # uint64 index in rusage_info_v2 (uuid occupies 0-1)


def footprint(pid: int) -> int:
    """Real phys_footprint (bytes) of one process, as Activity Monitor shows it (includes
    Metal/GPU buffers). 0 if the pid is gone or inaccessible."""
    buf = (ctypes.c_uint64 * 32)()
    if _libproc.proc_pid_rusage(pid, _RUSAGE_INFO_V2, ctypes.byref(buf)) != 0:
        return 0
    return buf[_PHYS_FOOTPRINT_IDX]


def available_bytes() -> int:
    """System-wide memory available for new allocations without swapping (vm_stat)."""
    out = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
    page = int(re.search(r"page size of (\d+)", out).group(1))
    pages = {k: int(v) for k, v in re.findall(r"Pages (\w+):\s+(\d+)", out)}
    return page * sum(pages.get(k, 0) for k in ("free", "inactive", "speculative", "purgeable"))


def total_bytes() -> int:
    """Total physical memory installed (sysctl hw.memsize)."""
    out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout
    return int(out.strip())
