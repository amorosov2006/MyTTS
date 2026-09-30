"""GPU worker: shared batch-processing logic (process_batch), the subprocess entry point, and
the two TTSWorker implementations (ProcessWorker for real use, InProcessWorker for tests).

Memory safety (PLAN.md / CLAUDE.md): the real engine only ever runs inside the child process
spawned by ProcessWorker, one at a time, supervised against real phys_footprint (mytts.pipeline
.memguard), never mx.get_peak_memory(). Implementer agents must run `pytest -m gpu` under
bench/memguard.py.
"""
from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import signal
import time
from typing import Optional

import numpy as np
import soundfile as sf

from mytts import config
from mytts.contracts import (
    Engine,
    Lang,
    SynthesisItem,
    SynthesisParams,
    SynthesisResult,
    Verifier,
    Voice,
    WorkerCrashed,
    WorkerStatus,
)
from mytts.pipeline.memguard import available_bytes, footprint

_ctx = mp.get_context("spawn")


def _write_wav(path: str, audio: np.ndarray, sample_rate: int) -> float:
    sf.write(path, np.asarray(audio, dtype=np.float32), sample_rate, subtype="PCM_16")
    return len(audio) / sample_rate


# --------------------------------------------------------------------------------- process_batch

def process_batch(engine: Engine, verifier: Optional[Verifier], items: list[SynthesisItem],
                   voice: Voice, params: SynthesisParams, qa: bool) -> list[SynthesisResult]:
    """Synthesize a batch (assumed same language, contracts.TTSWorker.synthesize's contract),
    optionally QA-verifying and regenerating failures, keeping the best attempt per item.

    - A batch call that raises falls back to per-item synthesis once, to isolate the bad item.
    - An item is regenerated (in a smaller batch of just the still-failing items) while its best
      attempt so far hit_token_cap, or (with qa) has cer > config.QA_MAX_CER, up to
      config.QA_MAX_ATTEMPTS total attempts.
    - ok=False only if every attempt for that item failed hard (no audio ever produced); a
      low-quality best attempt is still ok=True, with its cer reported.
    Never raises for per-item failures.
    """
    if not items:
        return []
    lang = items[0].lang
    sample_rate = engine.sample_rate

    # best[segment_id] -> mutable record of the best attempt seen so far
    best: dict[str, dict] = {
        it.segment_id: {"ok": False, "score": float("inf"), "attempts": 0, "error": None,
                         "audio": None, "cer": None, "transcript": None}
        for it in items
    }
    pending = list(items)
    attempt = 1

    while pending and attempt <= config.QA_MAX_ATTEMPTS:
        texts = [it.text for it in pending]
        try:
            outputs = engine.synthesize(texts, lang, voice, params)
        except Exception:
            # Isolate the bad item(s) with a per-item retry of this same attempt.
            outputs = []
            for it in pending:
                try:
                    outputs.extend(engine.synthesize([it.text], lang, voice, params))
                except Exception as e2:
                    outputs.append(e2)

        still_pending = []
        for it, out in zip(pending, outputs):
            record = best[it.segment_id]
            record["attempts"] = attempt

            if isinstance(out, BaseException):
                record["error"] = str(out)
                still_pending.append(it)
                continue

            cer = transcript = None
            needs_retry = out.hit_token_cap
            if qa and verifier is not None:
                cer, transcript = verifier.check(out.audio, sample_rate, it.text, lang)
                needs_retry = needs_retry or cer > config.QA_MAX_CER
            score = cer if cer is not None else (1.0 if needs_retry else 0.0)

            if not record["ok"] or score < record["score"]:
                record.update(ok=True, score=score, audio=out.audio, cer=cer,
                              transcript=transcript, error=None)
            if needs_retry:
                still_pending.append(it)

        pending = still_pending
        attempt += 1

    results = []
    for it in items:
        r = best[it.segment_id]
        if r["ok"]:
            duration = _write_wav(it.out_wav, r["audio"], sample_rate)
            results.append(SynthesisResult(segment_id=it.segment_id, ok=True, out_wav=it.out_wav,
                                            duration_s=duration, attempts=r["attempts"],
                                            cer=r["cer"], transcript=r["transcript"]))
        else:
            results.append(SynthesisResult(segment_id=it.segment_id, ok=False,
                                            attempts=r["attempts"], error=r["error"]))
    return results


# --------------------------------------------------------------------------------- engine factory

def _make_engine(name: str) -> Engine:
    if name == "fake":
        from mytts.tts.fake import FakeEngine
        return FakeEngine()
    if name == "qwen":
        from mytts.tts.qwen_mlx import QwenEngine
        return QwenEngine()
    raise ValueError(f"unknown engine {name!r}")


def _make_verifier(name: str) -> Verifier:
    if name == "fake":
        from mytts.tts.fake import FakeVerifier
        return FakeVerifier()
    if name == "qwen":
        from mytts.tts.whisper_qa import WhisperVerifier
        return WhisperVerifier()
    raise ValueError(f"unknown engine {name!r}")


class _FakeDesigner:
    """Stand-in for VoiceDesigner when engine == "fake" (no GPU / models required)."""

    sample_rate = config.SAMPLE_RATE

    def load(self) -> None:
        pass

    def unload(self) -> None:
        pass

    def generate_voice_design(self, text: str, instruct: str, language: str) -> np.ndarray:
        seconds = max(0.3, len(text) / 14.0)
        n = int(seconds * self.sample_rate)
        return (0.1 * np.sin(2 * np.pi * 220 * np.arange(n) / self.sample_rate)).astype(np.float32)


def _make_designer(name: str):
    if name == "fake":
        return _FakeDesigner()
    if name == "qwen":
        from mytts.tts.qwen_mlx import VoiceDesigner
        return VoiceDesigner()
    raise ValueError(f"unknown engine {name!r}")


# --------------------------------------------------------------------------------- child process

def _start_child_watchdog(parent_pid: int, mem_cap_gb: float) -> None:
    """Daemon thread in the worker child: exit immediately if the app dies (even mid-batch,
    when the request loop isn't polling) or if our own footprint passes the cap — a second
    line of defence behind the parent's supervisor."""
    import threading

    def watch() -> None:
        while True:
            if os.getppid() != parent_pid:
                os._exit(3)
            if footprint(os.getpid()) > mem_cap_gb * 1e9 * 1.1:
                os._exit(4)
            time.sleep(0.5)

    threading.Thread(target=watch, daemon=True, name="mytts-watchdog").start()


def _child_main(conn, engine_name: str, mem_cap_gb: float = config.WORKER_MEM_CAP_GB) -> None:
    """Runs inside the spawned worker process. Request/response protocol over `conn`:
    ops synthesize / design_voice / status / shutdown, plus test-only ops test_alloc / test_sleep
    used by tests to simulate a memory blow-up or a hang without needing the real engine.
    Exits on its own if the parent dies without a clean shutdown (no orphans)."""
    parent_pid = os.getppid()
    _start_child_watchdog(parent_pid, mem_cap_gb)
    engine: Optional[Engine] = None
    verifier: Optional[Verifier] = None
    leaks: list[bytearray] = []  # test_alloc: keep allocations alive to raise our own footprint

    def get_engine() -> Engine:
        nonlocal engine
        if engine is None:
            need = mem_cap_gb * 0.6 + config.MIN_SYSTEM_FREE_GB
            if engine_name != "fake" and available_bytes() / 1e9 < need:
                raise MemoryError(f"not enough free memory to load the TTS model "
                                  f"({available_bytes() / 1e9:.1f} GB available, need {need:.1f} GB)")
            engine = _make_engine(engine_name)
            engine.load()
        return engine

    def get_verifier() -> Verifier:
        nonlocal verifier
        if verifier is None:
            verifier = _make_verifier(engine_name)
        return verifier

    last_activity = time.monotonic()
    try:
        while True:
            if not conn.poll(1.0):
                if os.getppid() != parent_pid:
                    return  # parent gone; do not become an orphan
                if (engine is not None or verifier is not None) and \
                        time.monotonic() - last_activity > config.WORKER_IDLE_UNLOAD_S:
                    if engine is not None:
                        engine.unload()
                    engine = verifier = None
                    import gc
                    gc.collect()
                continue
            last_activity = time.monotonic()
            try:
                req = conn.recv()
            except (EOFError, OSError):
                return

            op = req.get("op")
            try:
                if op == "shutdown":
                    conn.send({"ok": True, "result": None})
                    return
                elif op == "status":
                    conn.send({"ok": True, "result": {"loaded": engine is not None}})
                elif op == "synthesize":
                    items = [SynthesisItem(**d) for d in req["items"]]
                    voice = Voice(**req["voice"])
                    params = SynthesisParams(**req["params"])
                    qa = req["qa"]
                    v = get_verifier() if qa else None
                    results = process_batch(get_engine(), v, items, voice, params, qa)
                    conn.send({"ok": True, "result": [r.model_dump(mode="json") for r in results]})
                elif op == "design_voice":
                    if engine is not None:
                        engine.unload()
                        engine = None
                    designer = _make_designer(engine_name)
                    designer.load()
                    audio = designer.generate_voice_design(
                        req["text"], instruct=req["description"], language=req["lang"])
                    designer.unload()
                    duration = _write_wav(req["out_wav"], audio, designer.sample_rate)
                    conn.send({"ok": True, "result": duration})
                elif op == "test_alloc":
                    buf = bytearray(int(req["gb"] * (1 << 30)))
                    buf[::4096] = b"\x01" * len(range(0, len(buf), 4096))  # touch pages: real footprint
                    leaks.append(buf)
                    time.sleep(req.get("hold_s", 3.0))  # like a runaway generation, still busy
                    conn.send({"ok": True, "result": None})
                elif op == "test_sleep":
                    time.sleep(req["seconds"])
                    conn.send({"ok": True, "result": None})
                else:
                    conn.send({"ok": False, "error": f"unknown op {op!r}"})
            except Exception as e:
                conn.send({"ok": False, "error": f"{type(e).__name__}: {e}"})
    finally:
        conn.close()


# --------------------------------------------------------------------------------- ProcessWorker

class ProcessWorker:
    """Main-process handle to the real GPU worker: a supervised subprocess with a memory guard.
    Implements contracts.TTSWorker."""

    def __init__(self, engine: str = "qwen", mem_cap_gb: float = config.WORKER_MEM_CAP_GB,
                 call_timeout_s: float = 900.0, poll_interval_s: float = 0.5):
        self.engine_name = engine
        self.mem_cap_gb = mem_cap_gb
        self.call_timeout_s = call_timeout_s
        self.poll_interval_s = poll_interval_s

        self._proc = None
        self._conn = None
        self._lock = asyncio.Lock()
        self._supervisor_task: Optional[asyncio.Task] = None
        self._state = "stopped"
        self._footprint_gb = 0.0
        self._restarts = 0
        self._message: Optional[str] = None
        self._starting_lock = asyncio.Lock()
        self._gen = 0  # incremented per child process; stale supervisors/calls compare against it

    # ------------------------------------------------------------------- status

    def status(self) -> WorkerStatus:
        return WorkerStatus(
            state=self._state, footprint_gb=round(self._footprint_gb, 3),
            system_available_gb=round(available_bytes() / 1e9, 3), restarts=self._restarts,
            model=self.engine_name if self._state not in ("stopped", "failed") else None,
            message=self._message,
        )

    # ------------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        async with self._starting_lock:
            avail = available_bytes() / 1e9
            need = self.mem_cap_gb * 0.6 + config.MIN_SYSTEM_FREE_GB
            if avail < need:
                self._state = "failed"
                self._message = (f"refusing to start: only {avail:.1f} GB available, "
                                  f"need {need:.1f} GB headroom (cap {self.mem_cap_gb} GB)")
                raise WorkerCrashed(self._message)

            self._state = "starting"
            self._message = None
            parent_conn, child_conn = _ctx.Pipe()
            proc = _ctx.Process(target=_child_main, args=(child_conn, self.engine_name, self.mem_cap_gb),
                                daemon=True)
            proc.start()
            child_conn.close()
            self._proc = proc
            self._conn = parent_conn
            self._footprint_gb = 0.0
            self._state = "idle"
            self._gen += 1
            old = self._supervisor_task
            if old is not None and old is not asyncio.current_task():
                old.cancel()
            self._supervisor_task = asyncio.create_task(self._supervise(self._gen))

    def _kill(self) -> None:
        if self._proc is not None:
            if self._proc.is_alive():
                try:
                    os.kill(self._proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self._proc.join(timeout=5)
            self._proc = None
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None

    async def stop(self) -> None:
        if self._supervisor_task is not None:
            self._supervisor_task.cancel()
            try:
                await self._supervisor_task
            except (asyncio.CancelledError, Exception):
                pass
            self._supervisor_task = None

        if self._proc is not None and self._proc.is_alive():
            try:
                self._conn.send({"op": "shutdown"})
            except OSError:
                pass
            await asyncio.to_thread(self._proc.join, 5)
        await asyncio.to_thread(self._kill)
        self._state = "stopped"
        self._message = None

    async def _crash(self, reason: str) -> None:
        if self._state in ("restarting", "stopped"):
            return
        self._state = "restarting"
        self._message = reason
        await asyncio.to_thread(self._kill)
        self._restarts += 1
        try:
            await self.start()
        except WorkerCrashed as e:
            self._state = "failed"
            self._message = str(e)

    async def _supervise(self, gen: int) -> None:
        try:
            while True:
                await asyncio.sleep(self.poll_interval_s)
                proc = self._proc
                if proc is None or gen != self._gen:
                    return
                if not proc.is_alive():
                    if self._state not in ("restarting", "stopped", "failed"):
                        await self._crash("worker process exited unexpectedly")
                    return
                try:
                    fp = footprint(proc.pid) / 1e9
                    avail = available_bytes() / 1e9
                except Exception as e:  # can't measure -> can't guarantee the cap: fail safe
                    await self._crash(f"memory check failed: {e}")
                    return
                self._footprint_gb = fp
                if fp > self.mem_cap_gb:
                    await self._crash(f"footprint {fp:.1f} GB > cap {self.mem_cap_gb} GB")
                    return
                if avail < config.MIN_SYSTEM_FREE_GB:
                    await self._crash(
                        f"system available {avail:.1f} GB < reserve {config.MIN_SYSTEM_FREE_GB} GB")
                    return
        except asyncio.CancelledError:
            return

    # ------------------------------------------------------------------- calls

    def _send_recv(self, req: dict) -> dict:
        self._conn.send(req)
        return self._conn.recv()

    async def _call(self, req: dict):
        async with self._lock:
            if (self._proc is None or not self._proc.is_alive()) and self._state in ("stopped", "failed"):
                await self.start()  # raises WorkerCrashed (with the reason) if still no headroom
            if self._proc is not None and (self._supervisor_task is None or self._supervisor_task.done()):
                self._supervisor_task = asyncio.create_task(self._supervise(self._gen))
            if self._proc is None or not self._proc.is_alive():
                raise WorkerCrashed("worker not running")
            self._state = "busy"
            gen = self._gen
            try:
                resp = await asyncio.wait_for(
                    asyncio.to_thread(self._send_recv, req), timeout=self.call_timeout_s)
            except asyncio.TimeoutError as e:
                if gen == self._gen:
                    await self._crash(f"call timed out after {self.call_timeout_s}s")
                raise WorkerCrashed(f"call timed out after {self.call_timeout_s}s") from e
            except (EOFError, OSError, BrokenPipeError) as e:
                # The supervisor may already have killed and restarted the child (gen changed):
                # don't kill the fresh one.
                if gen == self._gen and self._state != "restarting":
                    await self._crash(f"worker connection lost: {e}")
                raise WorkerCrashed(self._message or f"worker connection lost: {e}") from e

            if self._state == "busy":
                self._state = "idle"
            if not resp.get("ok"):
                raise RuntimeError(resp.get("error", "worker error"))
            return resp["result"]

    async def synthesize(self, items: list[SynthesisItem], voice: Voice, params: SynthesisParams,
                          qa: bool) -> list[SynthesisResult]:
        req = {"op": "synthesize", "items": [i.model_dump(mode="json") for i in items],
               "voice": voice.model_dump(mode="json"), "params": params.model_dump(mode="json"),
               "qa": qa}
        result = await self._call(req)
        return [SynthesisResult(**r) for r in result]

    async def design_voice(self, description: str, lang: Lang, text: str, out_wav: str) -> float:
        req = {"op": "design_voice", "description": description, "lang": lang.value,
               "text": text, "out_wav": out_wav}
        return await self._call(req)

    # ------------------------------------------------------------------- test-only hooks

    async def _debug(self, op: str, **kwargs) -> None:
        """Not part of contracts.TTSWorker: lets tests trigger the memory-cap-kill / timeout
        paths against engine="fake" without touching the real model."""
        await self._call({"op": op, **kwargs})


# --------------------------------------------------------------------------------- InProcessWorker

class InProcessWorker:
    """TTSWorker for tests: runs process_batch in a thread, no subprocess. Implements
    contracts.TTSWorker."""

    def __init__(self, engine: Engine, verifier: Optional[Verifier] = None):
        self.engine = engine
        self.verifier = verifier
        self._state = "stopped"

    async def start(self) -> None:
        await asyncio.to_thread(self.engine.load)
        self._state = "idle"

    async def stop(self) -> None:
        await asyncio.to_thread(self.engine.unload)
        self._state = "stopped"

    def status(self) -> WorkerStatus:
        return WorkerStatus(state=self._state, footprint_gb=0.0,
                             system_available_gb=round(available_bytes() / 1e9, 3))

    async def synthesize(self, items: list[SynthesisItem], voice: Voice, params: SynthesisParams,
                          qa: bool) -> list[SynthesisResult]:
        self._state = "busy"
        try:
            return await asyncio.to_thread(
                process_batch, self.engine, self.verifier, items, voice, params, qa)
        finally:
            self._state = "idle"

    async def design_voice(self, description: str, lang: Lang, text: str, out_wav: str) -> float:
        designer = getattr(self.engine, "generate_voice_design", None)
        if designer is None:
            raise NotImplementedError(f"{type(self.engine).__name__} has no voice design support")
        audio = await asyncio.to_thread(designer, text, description, lang.value)
        return await asyncio.to_thread(_write_wav, out_wav, audio, self.engine.sample_rate)
