import socket
import threading
import time
from pathlib import Path

import httpx
import numpy as np
import pytest
import soundfile as sf
import uvicorn
from fastapi.testclient import TestClient

from mytts.api.app import create_app
from tests.fixtures.fake_worker import FakeWorker

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def worker():
    return FakeWorker()


@pytest.fixture
def client(fake_services, worker, tmp_path):
    app = create_app(services=fake_services, worker=worker, data_dir=tmp_path / "data")
    with TestClient(app) as c:
        yield c


def _upload(client, name="sample_book.txt", content: bytes = None):
    content = content if content is not None else (FIXTURES / "sample_book.txt").read_bytes()
    return client.post("/api/jobs", files={"file": (name, content, "text/plain")})


def _wait_for(fn, predicate, timeout=10.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if predicate(value):
            return value
        time.sleep(interval)
    raise AssertionError(f"condition not met before timeout, last value: {fn()}")


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "version": "0.1.0"}


def test_system(client):
    r = client.get("/api/system")
    assert r.status_code == 200
    body = r.json()
    assert body["memory"]["cap_gb"] == 30
    assert body["memory"]["total_gb"] == pytest.approx(48.0)
    assert body["worker"]["state"] == "idle"
    assert "voice_id" in body["defaults"]


def test_formats(client):
    r = client.get("/api/formats")
    assert r.status_code == 200
    assert "extensions" in r.json()


def test_voices_list_and_preview(client):
    r = client.get("/api/voices")
    assert r.status_code == 200
    voices = r.json()
    ids = {v["id"] for v in voices}
    assert {"ru_male", "ru_female", "en_male", "en_female"} <= ids
    for v in voices:
        assert "ref_audio" not in v
        assert v["preview_url"] == f"/api/voices/{v['id']}/preview"
    r2 = client.get("/api/voices/ru_male/preview")
    assert r2.status_code == 200
    assert r2.headers["content-type"] == "audio/wav"


def test_delete_builtin_voice_forbidden(client):
    r = client.delete("/api/voices/ru_male")
    assert r.status_code == 403


def test_delete_missing_voice_404(client):
    r = client.delete("/api/voices/does_not_exist")
    assert r.status_code == 404


def test_clone_voice(client, tmp_path):
    wav_path = tmp_path / "clip.wav"
    sr = 24000
    t = np.linspace(0, 10.0, int(sr * 10.0), endpoint=False)
    audio = (0.05 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
    sf.write(wav_path, audio, sr)
    with open(wav_path, "rb") as f:
        r = client.post("/api/voices/clone", files={"audio": ("clip.wav", f, "audio/wav")},
                        data={"transcript": "hello world", "name": "My Clone", "lang": "en"})
    assert r.status_code == 201, r.text
    voice = r.json()
    assert voice["builtin"] is False
    assert voice["name"] == "My Clone"

    r2 = client.get("/api/voices")
    assert any(v["id"] == voice["id"] for v in r2.json())
    r3 = client.delete(f"/api/voices/{voice['id']}")
    assert r3.status_code == 204


def test_clone_voice_rejects_too_short_clip(client, tmp_path):
    wav_path = tmp_path / "short.wav"
    sr = 24000
    audio = np.zeros(int(sr * 1.0), dtype=np.float32)
    sf.write(wav_path, audio, sr)
    with open(wav_path, "rb") as f:
        r = client.post("/api/voices/clone", files={"audio": ("short.wav", f, "audio/wav")},
                        data={"transcript": "hi", "name": "Too Short", "lang": "en"})
    assert r.status_code == 422


def test_voice_design_success_and_conflict_when_job_running(client, worker):
    r = client.post("/api/voices/design",
                    json={"name": "New Voice", "lang": "ru", "description": "a calm narrator"})
    assert r.status_code == 202
    voice_id = r.json()["voice_id"]

    def _has_voice():
        return any(v["id"] == voice_id for v in client.get("/api/voices").json())

    _wait_for(lambda: _has_voice(), lambda ok: ok, timeout=5)
    assert len(worker.design_calls) == 1

    job = _upload(client).json()
    client.post(f"/api/jobs/{job['id']}/start")
    _wait_for(lambda: client.get(f"/api/jobs/{job['id']}").json()["status"],
              lambda s: s == "running", timeout=5)
    r2 = client.post("/api/voices/design",
                     json={"name": "V2", "lang": "en", "description": "another voice"})
    assert r2.status_code == 409


def test_upload_creates_parsed_job(client):
    r = _upload(client)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "parsed"
    assert len(job["chapters"]) == 4
    assert job["title"] == "sample_book"

    r2 = client.get(f"/api/jobs/{job['id']}")
    assert r2.status_code == 200
    assert r2.json()["id"] == job["id"]

    r3 = client.get("/api/jobs")
    assert any(j["id"] == job["id"] for j in r3.json())


def test_upload_bad_format_returns_422(client):
    r = _upload(client, name="book.bin", content=b"\x00\x01not a book")
    assert r.status_code == 422
    assert "detail" in r.json()


def test_job_not_found_404(client):
    assert client.get("/api/jobs/job_missing").status_code == 404
    assert client.post("/api/jobs/job_missing/start").status_code == 404
    assert client.delete("/api/jobs/job_missing").status_code == 404


def test_chapter_text_and_cover_and_patch(client):
    job = _upload(client).json()
    jid = job["id"]
    r = client.get(f"/api/jobs/{jid}/chapters/0/text")
    assert r.status_code == 200
    assert "paragraphs" in r.json()
    assert client.get(f"/api/jobs/{jid}/chapters/99/text").status_code == 404
    assert client.get(f"/api/jobs/{jid}/cover").status_code == 404  # fake book has no cover

    r2 = client.patch(f"/api/jobs/{jid}/chapters",
                      json=[{"index": 1, "include": False, "title": "Renamed"}])
    assert r2.status_code == 200
    chapters = r2.json()["chapters"]
    assert chapters[1]["include"] is False and chapters[1]["title"] == "Renamed"


def test_settings_put_conflict_after_running(client):
    job = _upload(client).json()
    jid = job["id"]
    settings = job["settings"]
    settings["speed"] = 1.2
    r = client.put(f"/api/jobs/{jid}/settings", json=settings)
    assert r.status_code == 200 and r.json()["settings"]["speed"] == 1.2

    client.post(f"/api/jobs/{jid}/start")
    _wait_for(lambda: client.get(f"/api/jobs/{jid}").json()["status"], lambda s: s == "running")
    r2 = client.put(f"/api/jobs/{jid}/settings", json=settings)
    assert r2.status_code == 409


def test_full_run_via_api_and_chapter_audio_range(client):
    job = _upload(client).json()
    jid = job["id"]
    r = client.post(f"/api/jobs/{jid}/start")
    assert r.status_code == 200

    final = _wait_for(lambda: client.get(f"/api/jobs/{jid}").json(),
                      lambda j: j["status"] == "done", timeout=15)
    assert final["progress"]["segments_done"] == final["progress"]["segments_total"]

    r2 = client.get(f"/api/jobs/{jid}/chapters/0/audio")
    assert r2.status_code == 200
    assert r2.headers["content-type"] == "audio/mpeg"
    full_body = r2.content
    assert len(full_body) > 100

    r3 = client.get(f"/api/jobs/{jid}/chapters/0/audio", headers={"Range": "bytes=0-99"})
    assert r3.status_code == 206
    assert r3.headers["content-range"].startswith("bytes 0-99/")
    assert len(r3.content) == 100

    segs = client.get(f"/api/jobs/{jid}/chapters/0/segments").json()
    assert len(segs) > 0
    seg_audio = client.get(segs[0]["url"])
    assert seg_audio.status_code == 200 and seg_audio.headers["content-type"] == "audio/wav"


def test_sample_flow_and_approve(client):
    job = _upload(client).json()
    jid = job["id"]
    r = client.post(f"/api/jobs/{jid}/samples", json={"seconds": 15})
    assert r.status_code == 202
    sample = r.json()

    done = _wait_for(lambda: client.get(f"/api/jobs/{jid}/samples").json(),
                     lambda samples: samples and samples[0]["status"] == "done", timeout=10)
    sid = done[0]["id"]
    assert done[0]["audio_url"]

    r2 = client.get(f"/api/jobs/{jid}/samples/{sid}/audio")
    assert r2.status_code == 200 and r2.headers["content-type"] == "audio/mpeg"

    r3 = client.post(f"/api/jobs/{jid}/samples/{sid}/approve")
    assert r3.status_code == 200
    assert r3.json()["sample_approved"] is True

    settings = job["settings"]
    settings["speed"] = 1.4
    client.put(f"/api/jobs/{jid}/settings", json=settings)
    assert client.get(f"/api/jobs/{jid}").json()["sample_approved"] is False


def test_pause_resume_cancel_via_api(client):
    job = _upload(client).json()
    jid = job["id"]
    client.post(f"/api/jobs/{jid}/start")
    _wait_for(lambda: client.get(f"/api/jobs/{jid}").json()["progress"]["segments_done"],
              lambda n: n >= 1, timeout=5)
    r = client.post(f"/api/jobs/{jid}/pause")
    assert r.status_code == 200 and r.json()["status"] == "paused"
    assert client.post(f"/api/jobs/{jid}/pause").status_code == 409

    r2 = client.post(f"/api/jobs/{jid}/resume")
    assert r2.status_code == 200
    _wait_for(lambda: client.get(f"/api/jobs/{jid}").json()["status"], lambda s: s == "done",
              timeout=15)

    job2 = _upload(client).json()
    jid2 = job2["id"]
    client.post(f"/api/jobs/{jid2}/start")
    r3 = client.post(f"/api/jobs/{jid2}/cancel")
    assert r3.status_code == 200 and r3.json()["status"] == "cancelled"


def test_delete_job(client):
    job = _upload(client).json()
    r = client.delete(f"/api/jobs/{job['id']}")
    assert r.status_code == 204
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404


def test_pick_folder(client, monkeypatch):
    class FakeCompleted:
        returncode = 0
        stdout = "/Users/test/Music/Audiobooks\n"

    monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeCompleted())
    r = client.post("/api/pick-folder", json={})
    assert r.status_code == 200
    assert r.json()["path"] == "/Users/test/Music/Audiobooks"


def test_pick_folder_cancelled(client, monkeypatch):
    class FakeCancelled:
        returncode = 1
        stdout = ""

    monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeCancelled())
    r = client.post("/api/pick-folder", json={})
    assert r.status_code == 200
    assert r.json()["path"] is None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_app(fake_services, tmp_path):
    """SSE streams never terminate, and httpx's ASGITransport (used by TestClient) buffers
    the *entire* response before returning it — it can't be used to test a live stream.
    A real uvicorn server over a real socket streams properly, so SSE tests use this instead."""
    worker = FakeWorker()
    app = create_app(services=fake_services, worker=worker, data_dir=tmp_path / "data")
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "uvicorn did not start in time"
    try:
        yield f"http://127.0.0.1:{port}", worker
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_sse_all_events_stream_snapshot_and_updates(live_app):
    base_url, _worker = live_app
    with httpx.Client(timeout=5) as http_client:
        job = http_client.post(
            f"{base_url}/api/jobs",
            files={"file": ("sample_book.txt", (FIXTURES / "sample_book.txt").read_bytes(),
                            "text/plain")},
        ).json()

        with http_client.stream("GET", f"{base_url}/api/events") as resp:
            assert resp.status_code == 200
            it = resp.iter_lines()
            lines = [next(it) for _ in range(4)]
            # first SSE message on connect is the job snapshot for the job we just created
            assert any(job["id"] in line for line in lines)

        http_client.post(f"{base_url}/api/jobs/{job['id']}/start")
        with http_client.stream("GET", f"{base_url}/api/events", timeout=15) as resp:
            it = resp.iter_lines()
            seen_segment_or_job_update = False
            for _ in range(50):
                line = next(it)
                if "\"type\":\"segment\"" in line or "\"type\":\"job\"" in line:
                    seen_segment_or_job_update = True
                    break
            assert seen_segment_or_job_update


def test_job_scoped_sse_stream_and_404(live_app):
    base_url, _worker = live_app
    with httpx.Client(timeout=5) as http_client:
        job = http_client.post(
            f"{base_url}/api/jobs",
            files={"file": ("sample_book.txt", (FIXTURES / "sample_book.txt").read_bytes(),
                            "text/plain")},
        ).json()
        jid = job["id"]

        with http_client.stream("GET", f"{base_url}/api/jobs/{jid}/events") as resp:
            assert resp.status_code == 200
            it = resp.iter_lines()
            lines = [next(it) for _ in range(2)]  # "event: job" then "data: {...}"
            assert any(jid in line for line in lines)

        assert http_client.get(f"{base_url}/api/jobs/job_missing/events").status_code == 404
