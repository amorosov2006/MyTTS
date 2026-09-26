import pytest

from mytts.config import VOICES_DIR
from mytts.contracts import Lang, Voice

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


@pytest.fixture
def voice_ru() -> Voice:
    import json
    meta = json.loads((VOICES_DIR / "ru_male" / "voice.json").read_text(encoding="utf-8"))
    return Voice(**{**meta, "lang": Lang(meta["lang"]), "ref_audio": str(VOICES_DIR / "ru_male" / "ref.wav")})


@pytest.fixture
def fixtures_dir():
    return FIXTURES
