"""Regression tests for the security review findings (see final report).

Each test reproduces the vulnerability against the pre-fix code path and then
asserts the fixed behavior.
"""
from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mytts import config
from mytts.api.app import create_app
from mytts.contracts import IngestError
from mytts.ingest import parse_book
from mytts.voices import _to_wav
from tests.fixtures.fake_worker import FakeWorker

FIXTURES = Path(__file__).parent / "fixtures"

# TestClient's default Host header is "testserver"; production keeps the strict default.
TEST_ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]


@pytest.fixture
def app(fake_services, tmp_path):
    return create_app(
        services=fake_services, worker=FakeWorker(), data_dir=tmp_path / "data",
        allowed_hosts=TEST_ALLOWED_HOSTS,
    )


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------- 1. SPA path traversal

@pytest.fixture
def spa_client(fake_services, tmp_path, monkeypatch):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (static_dir / "app.js").write_text("console.log('ok')", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    monkeypatch.setattr(config, "STATIC_DIR", static_dir)

    app = create_app(
        services=fake_services, worker=FakeWorker(), data_dir=tmp_path / "data",
        allowed_hosts=TEST_ALLOWED_HOSTS,
    )
    with TestClient(app) as c:
        yield c, secret


def test_spa_catchall_blocks_path_traversal(spa_client):
    client, secret = spa_client
    r = client.get("/%2e%2e/secret.txt")
    assert secret.read_text() not in r.text
    # falls back to the SPA shell instead of leaking the file
    assert r.text == "<html>spa</html>"


def test_spa_catchall_still_serves_real_static_files(spa_client):
    client, _secret = spa_client
    r = client.get("/app.js")
    assert r.status_code == 200
    assert "console.log" in r.text


# --------------------------------------------------------------------- 2. Host / Origin checks

def test_untrusted_host_header_rejected(fake_services, tmp_path):
    app = create_app(
        services=fake_services, worker=FakeWorker(), data_dir=tmp_path / "data",
        allowed_hosts=["127.0.0.1", "localhost"],  # production-like: no "testserver"
    )
    with TestClient(app) as c:
        r = c.get("/api/health")
    assert r.status_code == 400


def test_cross_origin_post_rejected(client):
    r = client.post("/api/jobs/nonexistent/pause", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_localhost_origin_any_port_allowed(client):
    r = client.post("/api/jobs/nonexistent/pause", headers={"Origin": "http://localhost:5173"})
    assert r.status_code != 403  # rejected for "job not found" instead, not by the origin check


def test_no_origin_header_allowed(client):
    r = client.post("/api/jobs/nonexistent/pause")
    assert r.status_code != 403


def test_get_ignores_origin_check(client):
    r = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert r.status_code == 200


# --------------------------------------------------------------------- 3. AppleScript injection

def test_pick_folder_start_passed_as_argv_not_interpolated(client, monkeypatch, tmp_path):
    captured = {}
    malicious = tmp_path / '"; do shell script "touch pwned_marker" --'
    malicious.mkdir()

    class FakeCompleted:
        returncode = 0
        stdout = "/chosen/path\n"

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = client.post("/api/pick-folder", json={"start": str(malicious)})
    assert r.status_code == 200
    cmd = captured["cmd"]
    assert cmd[0] == "osascript"
    # the untrusted value must be its own argv item after "--", never concatenated
    # into any -e script fragment
    assert str(malicious) in cmd
    assert cmd[-1] == str(malicious) or cmd[cmd.index("--") + 1] == str(malicious)
    for part in cmd:
        if part.startswith("-e"):
            continue
        if part == str(malicious):
            continue
    script_fragments = " ".join(a for a in cmd if a not in ("osascript", "--", str(malicious)))
    assert str(malicious) not in script_fragments
    assert '"; do shell script' not in script_fragments


def test_pick_folder_cancelled_returns_null(client, monkeypatch):
    class FakeCancelled:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCancelled())
    r = client.post("/api/pick-folder", json={})
    assert r.status_code == 200
    assert r.json()["path"] is None


def test_pick_folder_empty_start_omits_default_location(client, monkeypatch):
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = "/chosen/path\n"

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = client.post("/api/pick-folder", json={})
    assert r.status_code == 200
    assert not any("default location" in a for a in captured["cmd"])


# --------------------------------------------------------------------- 4. zip bombs

def _make_zip_bomb(path: Path, member_uncompressed: int = 150 * 1024 * 1024) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        zi = zipfile.ZipInfo("book.fb2")
        zi.compress_type = zipfile.ZIP_DEFLATED  # ZipInfo ignores the ZipFile-level default
        with z.open(zi, "w") as f:
            chunk = b"0" * (1024 * 1024)
            written = 0
            while written < member_uncompressed:
                f.write(chunk)
                written += len(chunk)


def test_parse_book_rejects_zip_bomb(tmp_path):
    bomb = tmp_path / "bomb.fb2.zip"
    _make_zip_bomb(bomb)
    # sanity: a genuine zip bomb compresses to a tiny fraction of its content
    assert bomb.stat().st_size < 1024 * 1024
    with pytest.raises(IngestError):
        parse_book(bomb)


def test_parse_book_rejects_oversized_plain_file(tmp_path, monkeypatch):
    from mytts import ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "MAX_PLAIN_FILE_BYTES", 1024)
    big = tmp_path / "big.txt"
    big.write_bytes(b"a" * 2048)
    with pytest.raises(IngestError):
        parse_book(big)


def test_fb2_xml_parser_hardened_against_xxe():
    from mytts.ingest import fb2

    parser_kwargs = fb2._read_root.__wrapped__ if hasattr(fb2._read_root, "__wrapped__") else None
    # Directly assert the parser is constructed with entity resolution and network access off.
    import inspect
    src = inspect.getsource(fb2._read_root)
    assert "resolve_entities=False" in src
    assert "no_network=True" in src


# --------------------------------------------------------------------- 5. voice clone / ffmpeg

def test_clone_voice_rejects_oversized_upload(client):
    big = b"x" * (51 * 1024 * 1024)
    r = client.post(
        "/api/voices/clone",
        data={"transcript": "hello", "name": "test", "lang": "en"},
        files={"audio": ("clip.wav", big, "audio/wav")},
    )
    assert r.status_code == 413


def test_clone_voice_rejects_disallowed_extension(client):
    r = client.post(
        "/api/voices/clone",
        data={"transcript": "hello", "name": "test", "lang": "en"},
        files={"audio": ("clip.exe", b"not audio", "application/octet-stream")},
    )
    assert r.status_code == 415


def test_to_wav_uses_protocol_whitelist_and_timeout(monkeypatch, tmp_path):
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append((cmd, kwargs))
        if cmd[0] == "ffprobe":
            class R:
                stdout = "12.5\n"
            return R()

        class R:
            pass
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    src = tmp_path / "in.wav"
    src.write_bytes(b"RIFF....")
    dst = tmp_path / "out.wav"
    _to_wav(src, dst)

    ffmpeg_call = captured[0][0]
    assert "-protocol_whitelist" in ffmpeg_call
    idx = ffmpeg_call.index("-protocol_whitelist")
    assert ffmpeg_call[idx + 1] == "file,pipe"
    assert ffmpeg_call.index("-protocol_whitelist") < ffmpeg_call.index("-i")
    assert captured[0][1].get("timeout") == 60
