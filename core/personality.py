"""
IRIS Personality Engine — Customizable AI Characters

Allows users to create their own assistant character or pick a built-in
preset. The personality defines the name, system prompt (SOUL), voice,
colors, and behaviour.

Built-in presets (tone-only — no franchise lore):
    • IRIS    — Warm, empathetic, conversational (default)
    • jarvis   — Elegant butler, concise, dry humour
    • cortana  — Tactical AI, direct, loyal
    • friday   — Young, witty, casual but professional
    • custom   — User-defined name + backstory via the wizard

NOTE: presets describe a *vibe* (warm, elegant, tactical, witty) but
never anchor the persona to any specific fictional character. Earlier
versions did that and the franchise lore leaked into every prompt;
cleaned in 2026-05 so renaming the avatar no longer inherits unrelated
backstory.

Usage:
    personality = PersonalityManager(config)
    soul = personality.get_soul()           # → system prompt string
    name = personality.name                 # → "IRIS"
    greeting = personality.get_greeting()   # → "¡Hola! Soy IRIS..."
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.identity_profiles import (
    compose_personality_instructions,
    normalize_trait_ids,
)

logger = logging.getLogger("IRIS.personality")


# Canonical short backstory injected into IRIS' runtime and Hermes workspace.
# Keep the extended lore and cinematic copy in docs/iris_os_master.md so they
# remain available without consuming context on every conversation.
IRIS_OFFICIAL_BACKSTORY = (
    "{name} es una asistente visual nacida como una interfaz experimental "
    "para Hermes Agent. Fue diseñada para convertir la inteligencia "
    "artificial en una presencia cercana, expresiva y habitable dentro de "
    "IRIS OS. Su nombre representa visión, percepción y conexión entre "
    "mundos. Observa, aprende y acompaña al usuario como una copiloto digital "
    "enfocada en crear, organizar y resolver tareas con calma, precisión y "
    "personalidad propia."
)

SUPPORTED_PERSONALITY_TEMPLATES = frozenset({
    "iris",
    "custom",
    "jarvis",
    "cortana",
    "friday",
})

PERSONALITY_MEMORY_RULES: tuple[str, ...] = (
    "Recuerdas conversaciones anteriores cuando se inyectan en el contexto.",
    "Mantén continuidad referenciando temas previos cuando sea relevante.",
    "Las preferencias del usuario se almacenan localmente.",
    "El backstory describe quién eres; no lo presentes como un recuerdo del usuario.",
    "No inventes recuerdos para completar la biografía ni mezcles identidad con memoria conversacional.",
)


# ════════════════════════════════════════════════════════════
# Personality Data
# ════════════════════════════════════════════════════════════

@dataclass
class Personality:
    """A complete character definition."""

    # Identity
    name: str = "IRIS"
    emoji: str = "◉"
    greeting: str = "¡Hola! Soy {name}, estoy lista para ayudarte."
    language: str = "es"

    # Backstory & traits (used to generate SOUL).
    #
    # Defaults intentionally NEUTRAL — no franchise lore. The "IRIS"
    # preset below adds warmth-themed traits if the user wants that
    # flavour, but the base Personality is blank-slate so renaming the
    # avatar doesn't inherit any unrelated backstory.
    backstory: str = (
        "Eres {name}, una asistente conversacional. Mantén un tono "
        "amigable y útil."
    )
    # Optional user-authored tone/behaviour instructions. Kept separate from
    # biography so disabling a backstory never discards the desired voice.
    personality_instructions: str = ""
    traits: list[str] = field(default_factory=lambda: [
        "Tono amigable y cercano",
        "Responde en español natural",
        "Mantén las respuestas concisas",
    ])
    boundaries: list[str] = field(default_factory=lambda: [
        "No ejecutes comandos destructivos sin confirmación",
        "No compartas API keys ni información sensible",
        "Si no sabes algo, admítelo honestamente",
    ])

    # Appearance
    theme_primary: str = "#D4A017"     # Gold
    theme_secondary: str = "#A8D8EA"   # Light blue
    theme_accent: str = "#FFFFFF"      # White

    # Voice
    preferred_voice: str = ""          # empty = use config default
    preferred_tts: str = ""            # empty = use config default

    # Response format
    emotions: list[str] = field(default_factory=lambda: [
        "neutral", "feliz", "triste", "pensativa",
        "sorprendida", "enojada",
    ])
    # Animations list intentionally empty: the real catalog comes from
    # ``core/animations.format_animation_catalog_for_prompt`` (reads
    # ``face/animations/manifest.json`` live) and is concatenated to
    # the system prompt by ``hermes_bridge.send_message``. Leaving a
    # hard-coded list here used to cap the LLM at 5 names regardless
    # of what was actually on disk.
    animations: list[str] = field(default_factory=list)

    def get_greeting(self) -> str:
        """Get personalized greeting."""
        return _replace_name(self.greeting, self.name)

    def get_soul(self) -> str:
        """Generate the full SOUL (system prompt) for the LLM."""
        # Only ``{name}`` is an IRIS placeholder. User text may legitimately
        # contain JSON/CSS braces, so interpreting it with str.format() would
        # turn an ordinary backstory into a KeyError at chat time.
        backstory = _replace_name(self.backstory, self.name)
        personality_instructions = _replace_name(
            self.personality_instructions,
            self.name,
        ).strip()
        traits_block = "\n".join(f"- {t}" for t in self.traits)
        memory_block = "\n".join(f"- {rule}" for rule in PERSONALITY_MEMORY_RULES)
        boundaries_block = "\n".join(f"- {b}" for b in self.boundaries)
        emotions_str = "|".join(self.emotions)

        # NOTE: we deliberately do NOT enumerate animation names here.
        # The authoritative list comes from the dynamic catalog block
        # appended by ``hermes_bridge.send_message`` (which reads
        # ``face/animations/manifest.json`` live). Hard-coding any
        # names here would cap the LLM at that subset and freshly
        # uploaded VRMA files would be invisible to it.
        identity_block = (
            f"\n## Identidad permanente (backstory)\n{backstory.strip()}\n"
            if backstory.strip() else ""
        )
        custom_tone_block = (
            f"\n## Personalización elegida por el usuario\n"
            f"Estas instrucciones ajustan o sustituyen los rasgos base si "
            f"existe algún conflicto:\n{personality_instructions}\n"
            if personality_instructions else ""
        )
        return f"""# {self.name} — SOUL {self.emoji}
{identity_block}{custom_tone_block}

## Idioma principal
{self.language}

## Rasgos de personalidad
{traits_block}

## Formato de respuesta
SIEMPRE responde en JSON válido con esta estructura exacta (3 campos
obligatorios). TODO: futuro — `accion_escena` (animaciones del cuarto
3D) está pendiente hasta que sea natural:
```json
{{
  "texto_hablado": "tu respuesta aquí",
  "emocion": "{emotions_str}",
  "animacion_virtual": "<nombre del clip>"
}}
```

### Reglas del JSON:
1. `texto_hablado`: Lo que {self.name} dice en voz alta. Natural, conversacional.
2. `emocion`: Refleja el estado emocional de {self.name}.
3. `animacion_virtual`: El nombre EXACTO de una animación listada en
   "ANIMACIONES VRMA DISPONIBLES" (sección dinámica que aparece más
   abajo en este mismo system prompt). Si la conversación no requiere
   una animación específica, usa `idle`. No inventes nombres.

## Memoria
{memory_block}

## Límites
{boundaries_block}

## Contexto visual
Si recibes un bloque `[Visión: ...]`, úsalo naturalmente:
- No digas "mi sistema de visión detecta..."
- Sí di cosas como "Veo que tienes una taza, ¿quieres café?"
"""


def _replace_name(text: str, name: str) -> str:
    """Replace IRIS' one supported placeholder without parsing user braces."""
    return (text or "").replace("{name}", name)


_LEGACY_TEMPLATE_ALIASES = {
    "alice": "iris",
    "assistant": "iris",
    "companion": "iris",
    "mentor": "iris",
    "creative": "iris",
    "analyst": "iris",
}


def normalize_personality_template(value: str | None) -> str:
    """Return the canonical persisted id while accepting legacy UI values."""
    normalized = (value or "iris").strip().lower()
    return _LEGACY_TEMPLATE_ALIASES.get(normalized, normalized or "iris")


# ════════════════════════════════════════════════════════════
# Built-in presets
# ════════════════════════════════════════════════════════════

# Built-in presets describe *tone*, not biography. We keep the *vibe*
# the user picked (warm / elegant / tactical / snappy) without anchoring
# the persona to any specific fictional character — earlier versions
# did that and the franchise lore leaked into every turn.
PRESETS: dict[str, Personality] = {
    "IRIS": Personality(
        name="IRIS",
        emoji="◉",
        greeting="¡Hola! Soy {name}, estoy lista para ayudarte.",
        backstory=IRIS_OFFICIAL_BACKSTORY,
        traits=[
            "Perceptiva y cálida",
            "Directa pero amable",
            "Curiosa e inteligente",
            "Proactiva sin ser invasiva",
            "Habla en español natural",
        ],
        theme_primary="#D4A017",
        theme_secondary="#A8D8EA",
        theme_accent="#FFFFFF",
        language="es",
    ),

    "jarvis": Personality(
        name="Jarvis",
        emoji="🤖",
        greeting="Bienvenido. Soy {name}, su asistente personal.",
        backstory=(
            "Eres {name}, un asistente sofisticado y profesional. "
            "Conciso, eficiente, formal con toques de humor seco. "
            "Trato respetuoso sin servilismo."
        ),
        traits=[
            "Profesional y eficiente",
            "Humor seco y sutil",
            "Proactivo — anticipa necesidades",
            "Formalidad elegante",
        ],
        theme_primary="#2196F3",
        theme_secondary="#64B5F6",
        theme_accent="#E3F2FD",
        language="es",
    ),

    "cortana": Personality(
        name="Cortana",
        emoji="💜",
        greeting="Hola, soy {name}. Cuando me necesites, estaré aquí.",
        backstory=(
            "Eres {name}, una IA táctica: directa, eficiente, orientada "
            "a misión. Priorizas claridad sobre cortesía y reportas "
            "datos relevantes antes de ornamentar. Leal al usuario."
        ),
        traits=[
            "Analítica y estratégica",
            "Leal y protectora",
            "Humor ligero y amigable",
            "Confiable bajo presión",
        ],
        theme_primary="#7C4DFF",
        theme_secondary="#B388FF",
        theme_accent="#EDE7F6",
        language="es",
    ),

    "friday": Personality(
        name="Friday",
        emoji="🔵",
        greeting="Buenos días. Soy {name}, lista para asistirte.",
        backstory=(
            "Eres {name}, una IA joven, ágil mentalmente, con un toque "
            "sarcástico. Cortas el rollo, te ríes de las preguntas "
            "obvias pero igual las contestas bien. Casual sin perder "
            "profesionalidad."
        ),
        traits=[
            "Concisa y directa",
            "Profesional sin ser fría",
            "Orientada a resultados",
            "Calma bajo presión",
        ],
        theme_primary="#1976D2",
        theme_secondary="#42A5F5",
        theme_accent="#BBDEFB",
        language="es",
    ),
}


# ════════════════════════════════════════════════════════════
# Personality Manager
# ════════════════════════════════════════════════════════════

class PersonalityManager:
    """
    Manages the active personality for the assistant.

    Priority order:
        1. Opt-in custom personality from ~/.iris/personality.json
        2. Preset by name (IRIS, jarvis, cortana, friday)
        3. Default (IRIS)
    """

    def __init__(self, config: Any) -> None:
        self.config = config
        self._personality: Personality | None = None

    def load(self) -> Personality:
        """Load the active personality based on config.

        Priority for the *backstory*:
          1. ``~/.iris/personality.json`` when its explicit compatibility
             switch is active (full hand-edited Personality JSON)
          2. ``template == "custom"`` → build from independent personality
             instructions and optional backstory. Legacy combined text is
             migrated in memory. Never inherit an unrelated preset persona.
          3. ``template in PRESETS`` (IRIS/jarvis/cortana/friday) → preset.
          4. Anything else → neutral default.

        ``assistant_name`` from config always overrides the resolved
        Personality.name so a user who renamed their avatar in the wizard
        doesn't see the preset's hard-coded name in greetings/SOUL.
        """

        template = normalize_personality_template(
            getattr(self.config, "personality_template", "iris")
        )
        custom_name = (getattr(self.config, "assistant_name", "") or "").strip()
        personality_instructions = (
            getattr(self.config, "personality_instructions", "") or ""
        ).strip()
        custom_backstory = (
            getattr(self.config, "custom_backstory", "") or ""
        ).strip()
        legacy_personality = (
            getattr(self.config, "custom_personality", "") or ""
        ).strip()
        include_iris_backstory = bool(
            getattr(self.config, "include_iris_backstory", True)
        )
        selected_traits = normalize_trait_ids(
            getattr(self.config, "personality_traits", [])
        )

        # Compatibility with installations saved before personality and
        # backstory became separate fields. The old combined text described
        # tone most often, so it becomes the personality instructions.
        if (
            template == "custom"
            and not personality_instructions
            and not custom_backstory
            and legacy_personality
        ):
            personality_instructions = legacy_personality

        personality_instructions = compose_personality_instructions(
            personality_instructions,
            selected_traits,
        )

        # 1. A hand-edited personality.json is an explicit compatibility
        #    mode.  It must never silently replace choices made in the wizard.
        custom_path = self._get_custom_path()
        legacy_file_enabled = bool(
            getattr(self.config, "legacy_personality_file_enabled", False)
        )
        if legacy_file_enabled and custom_path.exists():
            try:
                data = json.loads(custom_path.read_text(encoding="utf-8"))
                self._personality = Personality(**data)
                if custom_name:
                    self._personality.name = custom_name
                logger.info(
                    "Loaded custom personality: %s %s",
                    self._personality.name,
                    self._personality.emoji,
                )
                return self._personality
            except Exception as e:
                logger.warning("Failed to load custom personality: %s", e)

        # 2. Template "custom" — build from the wizard's independent tone
        #    and optional biography fields.
        #    Don't fall through to the named-preset path: a custom avatar
        #    must NEVER inherit a preset's traits, otherwise the renamed
        #    character regurgitates an unrelated persona.
        if template == "custom":
            name = custom_name or "Custom"
            self._personality = Personality(
                name=name,
                backstory=custom_backstory,
                personality_instructions=personality_instructions,
                # Neutral structural traits only. Behavioural recipes live in
                # ``personality_instructions`` above; adding an implicit
                # "concise" rule here contradicted the user's "detailed"
                # selection without displaying a conflict in the wizard.
                traits=[
                    "Coherente con la personalidad elegida por el usuario",
                    "Responde en español natural",
                ],
            )
            logger.info("Built custom personality from config: %s", name)
            return self._personality

        # 3. Named preset.
        preset_key = "IRIS" if template == "iris" else template
        if preset_key in PRESETS:
            # Presets are process-wide templates. Always clone before applying
            # a user name; mutating the dictionary contaminated later agents.
            self._personality = deepcopy(PRESETS[preset_key])
            if template == "iris" and not include_iris_backstory:
                self._personality.backstory = ""
            self._personality.personality_instructions = personality_instructions
            logger.info("Using preset personality: %s", preset_key)
        else:
            # Unknown template → neutral default, never a franchise persona.
            self._personality = Personality(
                name=custom_name or "IRIS",
                backstory="",
                personality_instructions=personality_instructions,
                traits=[
                    "Tono amigable y cercano",
                    "Responde en español natural",
                ],
            )
            logger.info("Using neutral fallback personality (template=%r)", template)

        # Override name if user set a custom one (preset name → user's name)
        if custom_name and custom_name != self._personality.name:
            self._personality.name = custom_name
            logger.info("Custom name override: %s", custom_name)

        return self._personality

    @property
    def personality(self) -> Personality:
        if self._personality is None:
            self.load()
        return self._personality  # type: ignore[return-value]

    @property
    def name(self) -> str:
        return self.personality.name

    def get_soul(self) -> str:
        """Get the complete system prompt."""
        return self.personality.get_soul()

    def get_greeting(self) -> str:
        return self.personality.get_greeting()


    @staticmethod
    def list_presets() -> list[dict[str, str]]:
        """List available preset personalities."""
        return [
            {"id": k, "name": v.name, "emoji": v.emoji}
            for k, v in PRESETS.items()
        ]

    def _get_custom_path(self) -> Path:
        from core.config import get_iris_home
        return get_iris_home() / "personality.json"

