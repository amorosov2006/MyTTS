from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mytts.config import VOICES_DIR
from mytts.contracts import Lang, Voice
from mytts.pipeline.events import EventBus
from mytts.pipeline.scheduler import Scheduler
from mytts.pipeline.services import Services
from mytts.pipeline.store import Store
from mytts.voices import VoiceRegistry
from tests.fixtures.fake_services import (
    fake_assemble, fake_available_bytes, fake_build_m4b, fake_detect_lang, fake_footprint,
    fake_parse_book, fake_prepare_chapter, fake_process_segment, fake_total_bytes,
)
from tests.fixtures.fake_worker import FakeWorker

FIXTURES = Path(__file__).parent / "fixtures"


def _load_voice(name: str) -> Voice:
    import json
    meta = json.loads((VOICES_DIR / name / "voice.json").read_text(encoding="utf-8"))
    return Voice(**{**meta, "lang": Lang(meta["lang"]), "ref_audio": str(VOICES_DIR / name / "ref.wav")})


@pytest.fixture
def voice_ru() -> Voice:
    return _load_voice("ru_male")


@pytest.fixture
def voice_en() -> Voice:
    return _load_voice("en_male")


@pytest.fixture
def fixtures_dir():
    return FIXTURES


@pytest.fixture
def fake_services() -> Services:
    return Services(
        parse_book=fake_parse_book, prepare_chapter=fake_prepare_chapter,
        detect_lang=fake_detect_lang, process_segment=fake_process_segment,
        assemble=fake_assemble, build_m4b=fake_build_m4b, footprint=fake_footprint,
        available_bytes=fake_available_bytes, total_bytes=fake_total_bytes,
    )


@pytest.fixture
def data_dir(tmp_path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def store(data_dir) -> Store:
    s = Store(db_path=data_dir / "mytts.db")
    yield s
    s.close()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def voices_registry(data_dir) -> VoiceRegistry:
    return VoiceRegistry(user_dir=data_dir / "user_voices")


@pytest.fixture
def fake_worker() -> FakeWorker:
    return FakeWorker()


@pytest.fixture
def scheduler_factory(store, fake_services, bus, voices_registry):
    """Build a Scheduler with a given worker; a thread pool stands in for the CPU
    ProcessPoolExecutor used in production so fixtures/closures don't need to be picklable."""
    created = []

    def _make(worker=None):
        sch = Scheduler(store, fake_services, bus, worker or FakeWorker(), voices=voices_registry,
                        executor=ThreadPoolExecutor(max_workers=3))
        created.append(sch)
        return sch

    yield _make
    for sch in created:
        sch._executor.shutdown(wait=True)


@pytest.fixture
def scheduler(scheduler_factory, fake_worker):
    return scheduler_factory(fake_worker)


@pytest.fixture
def sample_book_txt(data_dir) -> Path:
    dst = data_dir / "sample_book.txt"
    dst.write_text((FIXTURES / "sample_book.txt").read_text(encoding="utf-8"), encoding="utf-8")
    return dst
