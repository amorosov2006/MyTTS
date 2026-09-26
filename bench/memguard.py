"""Memory watchdog: run a command, kill its whole process group if memory gets dangerous.

uv run python bench/memguard.py --limit-gb 14 -- uv run python bench/bench.py ...

Measures the real macOS phys_footprint (what Activity Monitor shows, includes Metal/GPU
buffers) of every process in the group, plus system-wide available memory from vm_stat.
Kills (SIGKILL) if group footprint > --limit-gb or available memory < --min-free-gb.
"""
import argparse
import ctypes
import os
import re
import signal
import subprocess
import sys
import time

_libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
RUSAGE_INFO_V2 = 2
_PHYS_FOOTPRINT_IDX = 9  # uint64 index in rusage_info_v2 (uuid occupies 0-1)


def footprint(pid: int) -> int:
    buf = (ctypes.c_uint64 * 32)()
    if _libproc.proc_pid_rusage(pid, RUSAGE_INFO_V2, ctypes.byref(buf)) != 0:
        return 0
    return buf[_PHYS_FOOTPRINT_IDX]


def group_pids(pgid: int) -> list[int]:
    out = subprocess.run(["ps", "-axo", "pid=,pgid="], capture_output=True, text=True).stdout
    return [int(p) for p, g in (line.split() for line in out.splitlines()) if int(g) == pgid]


def available_bytes() -> int:
    out = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
    page = int(re.search(r"page size of (\d+)", out).group(1))
    pages = {k: int(v) for k, v in re.findall(r"Pages (\w+):\s+(\d+)", out)}
    return page * sum(pages.get(k, 0) for k in ("free", "inactive", "speculative", "purgeable"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-gb", type=float, default=14)
    ap.add_argument("--min-free-gb", type=float, default=8)
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd

    free0 = available_bytes() / 1e9
    if free0 < args.limit_gb + args.min_free_gb:
        sys.exit(f"memguard: refusing to start, only {free0:.1f} GB available "
                 f"(need limit {args.limit_gb} + reserve {args.min_free_gb})")

    proc = subprocess.Popen(cmd, start_new_session=True)
    peak, reason = 0, None
    while proc.poll() is None:
        used = sum(footprint(p) for p in group_pids(proc.pid))
        peak = max(peak, used)
        avail = available_bytes()
        if used > args.limit_gb * 1e9:
            reason = f"group footprint {used/1e9:.1f} GB > limit {args.limit_gb} GB"
        elif avail < args.min_free_gb * 1e9:
            reason = f"system available {avail/1e9:.1f} GB < reserve {args.min_free_gb} GB"
        if reason:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            break
        time.sleep(args.interval)
    msg = f"memguard: peak footprint {peak/1e9:.1f} GB, exit {proc.returncode}"
    print(msg + (f", KILLED: {reason}" if reason else ""), file=sys.stderr, flush=True)
    sys.exit(proc.returncode if not reason else 137)


if __name__ == "__main__":
    main()
