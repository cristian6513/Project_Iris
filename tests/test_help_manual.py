from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
HELP_HTML = ROOT / "wizard" / "ui" / "help.html"
HELP_CSS = ROOT / "wizard" / "ui" / "help.css"
WIZARD_HTML = ROOT / "wizard" / "ui" / "index.html"
WIZARD_JS = ROOT / "wizard" / "ui" / "wizard.js"


def _html() -> str:
    return HELP_HTML.read_text(encoding="utf-8")


def test_manual_navigation_matches_all_modules() -> None:
    html = _html()
    nav = re.search(r'<nav class="manual-nav">(.*?)</nav>', html, re.DOTALL)
    assert nav is not None

    nav_targets = re.findall(r'href="#([^"]+)"', nav.group(1))
    module_ids = re.findall(
        r'<section id="([^"]+)" class="[^"]*manual-module',
        html,
    )

    assert len(nav_targets) == 20  # Setup and the current desktop's everyday tasks, including Licencias.
    assert nav_targets == module_ids
    assert len(module_ids) == len(set(module_ids))


def test_manual_theme_tracks_every_installer_edition() -> None:
    html = _html()
    css = HELP_CSS.read_text(encoding="utf-8")
    expected = {
        "iris": "iris-os-bg-v1.png",
        "rose": "theme-sakura.png",
        "verdant": "theme-hacker.png",
        "sunrise": "theme-classic.png",
    }

    for theme, asset in expected.items():
        assert f"{theme}: {{ label:" in html
        assert asset in css
    for theme in ("rose", "verdant", "sunrise"):
        assert f'data-manual-theme="{theme}"' in css
    assert "fetch('/api/current-config')" in html
    assert "config.visual_theme" in html
    assert "overflow-x: hidden" in css


def test_manual_information_avoids_stale_voice_and_local_install_promises() -> None:
    html = _html()

    assert "Remota gratuita" not in html
    assert "male-qn-qingse" not in html
    assert "es-MX-CarmenNeural" not in html
    assert "Spanish_Wiselady" in html
    assert "no instala Ollama, LM Studio, vLLM, llama.cpp ni Fish-Speech" in html
    assert "Sin un servidor local activo que tenga un modelo disponible" in html
    assert "macOS Intel / otras combinaciones</span><b>—</b><i class=\"bad\">No compatibles" in html


def test_manual_official_links_are_direct_and_safely_opened() -> None:
    html = _html()

    for url in (
        "https://hermes-agent.nousresearch.com/docs",
        "https://ollama.com/download",
        "https://lmstudio.ai/download",
    ):
        pattern = rf'<a href="{re.escape(url)}" target="_blank" rel="noopener noreferrer">'
        assert re.search(pattern, html)


def test_manual_explains_the_narrow_hugging_face_voice_integration() -> None:
    html = _html()
    assert "HF speech-to-speech / Qwen3-TTS" in html
    assert "IRIS integra solo su handler Qwen3-TTS" in html
    assert "Hermes continúa siendo el único dueño" in html
    assert "https://github.com/huggingface/speech-to-speech" in html
    assert "GPU AMD de Windows no equivale a CUDA" in html


def test_wizard_provider_and_diagnostic_copy_matches_runtime_behavior() -> None:
    html = WIZARD_HTML.read_text(encoding="utf-8")

    assert "23+ proveedores" not in html
    assert "Chat real con el proveedor" not in html
    assert "hermes.json" not in html
    assert "config.yaml" in html
    assert "sin consumir tokens" in html
    assert "se sincronizan con el catálogo compatible con esta versión de IRIS" in html


def test_wizard_explains_cloud_secret_boundary_without_false_local_only_claim() -> None:
    html = WIZARD_HTML.read_text(encoding="utf-8")
    source = WIZARD_JS.read_text(encoding="utf-8")

    assert "Nunca se suben a internet" not in html
    assert "solo las envía al proveedor elegido" in html
    assert "no las sube a servidores propios" in html
    assert "Configuracion existente detectada" not in source
    assert "Configuración existente detectada" in source
