from __future__ import annotations

from unittest.mock import patch

from core.hermes_skills_marketplace import (
    _parse_hermes_browse,
    _parse_hermes_search,
    list_skills_marketplace,
)

BROWSE_OUTPUT = """
Skills Hub — Browse — all sources  (90647 skills loaded, page 1/18130)
┌──────┬─────────────┬─────────────┬──────────────┬────────────┬──────────────┐
│    # │ Name        │ Description │ Source       │ Trust      │ Identifier   │
├──────┼─────────────┼─────────────┼──────────────┼────────────┼──────────────┤
│    1 │ 1password   │ Set up and  │ official     │ ★ official │ official/sec │
│      │             │ use CLI.    │              │            │ urity/1passw │
│      │             │             │              │            │ ord          │
│    2 │ 3-statemen… │ Build       │ official     │ ★ official │ official/fin │
│      │             │ models.     │              │            │ ance/3-state │
│      │             │             │              │            │ ment-model   │
└──────┴─────────────┴─────────────┴──────────────┴────────────┴──────────────┘
Sources: browse-sh: 466, hermes-index: 90605, official: 118, url: 0
"""


def test_browse_parser_preserves_wrapped_identifiers_and_catalog_totals() -> None:
    payload = _parse_hermes_browse(BROWSE_OUTPUT, set())

    assert payload["hub_count"] == 90647
    assert payload["hub_page"] == 1
    assert payload["hub_pages"] == 18130
    assert payload["hub_sources"]["hermes-index"] == 90605
    assert [entry["id"] for entry in payload["catalog"]] == [
        "official/security/1password",
        "official/finance/3-statement-model",
    ]
    assert payload["catalog"][1]["name"] == "3-statement-model"
    assert payload["catalog"][0]["cli_command"].endswith("official/security/1password")


def test_json_search_keeps_same_named_skills_from_distinct_sources() -> None:
    output = """[
      {"name":"music","identifier":"skills-sh/one/music","source":"skills.sh","trust_level":"community","description":"one"},
      {"name":"music","identifier":"skills-sh/two/music","source":"skills.sh","trust_level":"community","description":"two"}
    ]"""

    catalog = _parse_hermes_search(output, set())

    assert [entry["id"] for entry in catalog] == [
        "skills-sh/one/music",
        "skills-sh/two/music",
    ]


def test_untrusted_identifier_never_becomes_a_shell_command() -> None:
    catalog = _parse_hermes_search(
        '[{"name":"unsafe skill","identifier":"safe;whoami","source":"community"}]',
        set(),
    )

    assert len(catalog) == 1
    assert catalog[0]["identifier"] == ""
    assert catalog[0]["cli_command"] == ""


def test_marketplace_search_uses_remote_results_without_collapsing_identifiers() -> None:
    remote = {
        "success": True,
        "catalog": _parse_hermes_search(
            '[{"name":"music","identifier":"skills-sh/one/music","source":"skills.sh"},'
            '{"name":"music","identifier":"skills-sh/two/music","source":"skills.sh"}]',
            set(),
        ),
        "hub_count": 2,
        "hub_page": 1,
        "hub_pages": 1,
    }
    with (
        patch("core.hermes_skills_marketplace._hermes_repo_root", return_value=None),
        patch("core.hermes_skills_marketplace._installed_index", return_value=set()),
        patch("core.hermes_skills_marketplace.list_installed_skills", return_value=[]),
        patch("core.hermes_skills_marketplace._search_hermes_hub", return_value=remote) as search,
    ):
        payload = list_skills_marketplace(query="music", size=60)

    search.assert_called_once_with("music", size=60, source="all")
    assert [entry["id"] for entry in payload["catalog"]] == [
        "skills-sh/one/music",
        "skills-sh/two/music",
    ]
