"""Plain speech projection of a reply; never used to rewrite chat or memory."""

from __future__ import annotations

import html
import re

# Pictographs, flags, skin tones and common standalone symbol emoji.
# Use an explicit BMP set: stripping entire symbol blocks erases math signs.
_EMOJI = re.compile(
    "[\U0001f000-\U0001faff"
    "⌚⌛⏩⏪⏫⏬⏰⏳◽◾☀☁☂☃☄☎☑☔☕☘☠☢☣☦☪☮☯☸☹☺"
    "♈♉♊♋♌♍♎♏♐♑♒♓♟♠♣♥♦♨♻♾♿⚒⚓⚔⚕⚖⚗⚙⚛⚜⚠⚡⚧"
    "⚪⚫⚰⚱⚽⚾⛄⛅⛈⛎⛏⛑⛓⛔⛩⛪⛰⛱⛲⛳⛴⛵⛷⛸⛹⛺⛽"
    "✂✅✈✉✊✋✌✍✏✒✔✝✡✨✳✴❄❇❌❎❓❔❕❗❣❤➰➿⬛⬜⭐⭕]"
)
_EMOJI_TEXT_PRESENTATION = re.compile(
    "[\u00a9\u00ae\u203c\u2049\u2122\u2139\u2194-\u2199\u21a9\u21aa"
    "\u231a\u231b\u2328\u23cf\u23e9-\u23f3\u23f8-\u23fa\u24c2"
    "\u25aa\u25ab\u25b6\u25c0\u25fb-\u25fe\u2934\u2935"
    "\u2640\u2642\u2795-\u2797\u27a1\u2b05-\u2b07\u2b1b\u2b1c"
    "\u2b50\u2b55\u3030\u303d\u3297\u3299]\ufe0f"
)
_EMOJI_EXTRAS = re.compile("[\u200d\ufe0e\ufe0f\u20e3\U000e0020-\U000e007f]")
_LINK = re.compile(r"!?\[([^\]\n]+)\]\((?:[^()\n]|\([^()\n]*\))*\)")
_EMPHASIS = re.compile(r"(?<!\w)(\*{1,3}|_{1,3}|~~)(?=\S)(.+?)(?<=\S)\1(?!\w)")


def text_for_speech(text: str) -> str:
    """Remove visual decorations while retaining words and meaningful notation.

    This runs once, immediately before synthesis, for every provider (including
    fallback). Empty/emoji-only replies need no audio request. Inline code and
    file names retain their content; backslashes become word separators.
    """
    value = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    # Keycap numbers often enumerate steps: keep their digit, not the glyph.
    value = re.sub(r"([0-9])\uFE0F?\u20E3", r"\1", value)
    value = re.sub(r"[#*]\uFE0F?\u20E3", " ", value)
    value = _EMOJI_TEXT_PRESENTATION.sub(" ", value)
    value = _EMOJI.sub(" ", value)
    value = _EMOJI_EXTRAS.sub("", value)
    value = _LINK.sub(r"\1", value)

    # Standalone fences carry no speech, but keep the code between/inside them.
    value = re.sub(r"(?m)^[ \t]{0,3}(?:`{3,}|~{3,})[a-zA-Z0-9_+.-]*[ \t]*$", "", value)
    value = re.sub(
        r"(?m)^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)*\|?[ \t]*$", "", value
    )
    value = re.sub(r"(?m)^[ \t]*(?:\*[ \t]*){3,}$|^[ \t]*(?:_[ \t]*){3,}$", "", value)
    # Remove paired emphasis without damaging identifiers or multiplication.
    for _ in range(2):
        value = _EMPHASIS.sub(r"\2", value)
    # Keep leading negative numbers/comparisons, while dropping list markers.
    value = re.sub(
        r"(?m)^[ \t]{0,3}(?:#{1,6}[ \t]+|[•●▪◦‣][ \t]+|[-*>][ \t]+(?![ \t]*\d))",
        "",
        value,
    )
    value = value.replace("`", "")
    value = re.sub(r"\\+", " ", value)
    value = value.replace("|", " ")
    value = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F\u200B\uFEFF]", " ", value)

    # Keep newlines as natural pauses and avoid gluing words around an emoji.
    value = re.sub(r"[^\S\n]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = re.sub(r" +([,.;:!?])", r"\1", value).strip()
    return value if any(char.isalnum() for char in value) else ""
