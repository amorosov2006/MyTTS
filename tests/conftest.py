import pytest

from mytts.config import VOICES_DIR
from mytts.contracts import Lang, Voice

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


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
