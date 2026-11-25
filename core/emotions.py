"""Iris's emotions: the names the agent may put in `emocion` (2026-11-24).

The face (face/js/emotions.js, pose_face.js) knows the same twelve. The first six were the only
ones until 2026-11-24; the newer six show best on MMD models (blush, heart or star eyes, a sweat
drop, ZZZ) and are mixed from the VRM presets on other avatars.
"""

from __future__ import annotations

IRIS_EMOTIONS: tuple[str, ...] = (
    "neutral", "feliz", "triste", "pensativa", "sorprendida", "enojada",
    "avergonzada", "cariñosa", "emocionada", "confundida", "nerviosa", "somnolienta",
)

# One line for the agent on when to use the newer ones; the first six explain themselves.
EMOTION_GUIDE = (
    "avergonzada = timidez o vergüenza; cariñosa = afecto o ternura; "
    "emocionada = entusiasmo; confundida = no entiendes algo; "
    "nerviosa = inquietud o apuro; somnolienta = sueño o cansancio"
)
