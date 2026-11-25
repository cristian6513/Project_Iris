"""Iris's twelve emotions reach the agent's instructions and the voice tags (2026-11-24)."""

from core import hermes_bridge
from core.emotions import EMOTION_GUIDE, IRIS_EMOTIONS
from core.personality import Personality
from core.tts import FishSpeechTTSProvider


def test_twelve_emotions_first_six_unchanged():
    assert len(IRIS_EMOTIONS) == 12
    assert IRIS_EMOTIONS[:6] == ("neutral", "feliz", "triste", "pensativa", "sorprendida", "enojada")
    for name in IRIS_EMOTIONS[6:]:
        assert name in EMOTION_GUIDE


def test_agent_contract_and_soul_list_every_emotion():
    assert "|".join(IRIS_EMOTIONS) in hermes_bridge._JSON_ENFORCEMENT
    soul = Personality(name="IRIS").get_soul()
    assert "|".join(IRIS_EMOTIONS) in soul
    assert EMOTION_GUIDE in soul


def test_voice_tags_cover_every_emotion_with_proven_tags_only():
    tags = FishSpeechTTSProvider.EMOTION_TAGS
    assert set(tags) == set(IRIS_EMOTIONS)
    proven = {tags[name] for name in IRIS_EMOTIONS[:6]}
    for name in IRIS_EMOTIONS[6:]:
        assert tags[name] in proven, name
