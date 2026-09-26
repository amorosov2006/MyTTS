from mytts.contracts import Engine, Lang, SynthesisParams
from mytts.tts.fake import FakeEngine


def test_fake_engine_protocol(voice_ru):
    e = FakeEngine(cap_texts=["b"])
    assert isinstance(e, Engine)
    e.load()
    out = e.synthesize(["Привет, мир.", "b"], Lang.ru, voice_ru, SynthesisParams())
    assert len(out) == 2 and out[0].audio.dtype.name == "float32"
    assert not out[0].hit_token_cap and out[1].hit_token_cap
