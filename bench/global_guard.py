"""Session-wide safety net: kill the largest MyTTS python process if all of them together exceed the cap.

uv run python bench/global_guard.py --cap-gb 28      (runs until killed; logs to stdout)
"""
import argparse
import os
import signal
import subprocess
import time

from memguard import available_bytes, footprint


def mytts_pythons() -> list[tuple[int, str]]:
    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    procs = []
    for line in out.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if "python" in cmd and "MyTTS" in cmd and "global_guard" not in cmd and int(pid) != os.getpid():
            procs.append((int(pid), cmd))
    return procs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap-gb", type=float, default=28)
    ap.add_argument("--min-free-gb", type=float, default=5)
    args = ap.parse_args()
    peak = 0.0
    while True:
        sizes = [(footprint(pid), pid, cmd) for pid, cmd in mytts_pythons()]
        total = sum(s for s, _, _ in sizes) / 1e9
        avail = available_bytes() / 1e9
        if total > peak + 1:
            peak = total
            print(f"{time.strftime('%H:%M:%S')} new peak total {total:.1f} GB, available {avail:.1f} GB", flush=True)
        if sizes and (total > args.cap_gb or avail < args.min_free_gb):
            size, pid, cmd = max(sizes)
            os.kill(pid, signal.SIGKILL)
            print(f"{time.strftime('%H:%M:%S')} KILLED pid {pid} ({size/1e9:.1f} GB): total {total:.1f} GB, "
                  f"available {avail:.1f} GB :: {cmd[:160]}", flush=True)
        time.sleep(1)


if __name__ == "__main__":
    main()
