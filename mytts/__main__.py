"""uv run python -m mytts [--port N] [--no-browser] [--fake]

Starts the FastAPI app on 127.0.0.1 (never any other interface), keeps the Mac awake for as
long as the server runs, and opens the browser once the server is ready.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import threading
import time
import webbrowser

import uvicorn

from mytts import config
from mytts.api.app import create_app


def _make_worker(fake: bool):
    if fake:
        from mytts.pipeline.worker import InProcessWorker
        from mytts.tts.fake import FakeEngine
        return InProcessWorker(FakeEngine())
    from mytts.pipeline.worker import ProcessWorker
    return ProcessWorker(engine="qwen")


def main() -> None:
    ap = argparse.ArgumentParser(prog="mytts")
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--fake", action="store_true",
                     help="run an in-process FakeEngine instead of the real GPU worker "
                          "(UI development, no model/GPU needed)")
    args = ap.parse_args()

    worker = _make_worker(args.fake)
    app = create_app(worker=worker)

    caffeinate = None
    try:
        caffeinate = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    except (FileNotFoundError, OSError):
        pass  # not on macOS, or caffeinate unavailable — degrade gracefully

    url = f"http://{config.HOST}:{args.port}"
    if not args.no_browser:
        def _open_when_ready() -> None:
            time.sleep(1.0)
            webbrowser.open(url)

        threading.Thread(target=_open_when_ready, daemon=True).start()

    try:
        uvicorn.run(app, host=config.HOST, port=args.port)
    finally:
        if caffeinate is not None:
            caffeinate.terminate()


if __name__ == "__main__":
    main()
