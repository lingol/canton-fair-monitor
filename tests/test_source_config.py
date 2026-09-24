import json

from canton_fair_alert.config import DEFAULT_OFFICIAL_URL, RETIRED_OFFICIAL_URL, load_sources


def test_new_installation_uses_working_default(tmp_path):
    assert load_sources(tmp_path / "absent.json")[0].url == DEFAULT_OFFICIAL_URL


def test_existing_source_config_is_upgraded_without_rewriting(tmp_path):
    path = tmp_path / "sources.json"
    payload = {
        "sources": [
            {"name": "primary", "url": RETIRED_OFFICIAL_URL, "priority": 2},
            {"name": "custom", "url": "https://www.cantonfair.org.cn/custom", "priority": 1},
            {"name": "disabled", "url": RETIRED_OFFICIAL_URL, "enabled": False},
        ]
    }
    original = json.dumps(payload)
    path.write_text(original)
    sources = load_sources(path)
    assert [(s.name, s.url) for s in sources] == [
        ("custom", "https://www.cantonfair.org.cn/custom"),
        ("primary", DEFAULT_OFFICIAL_URL),
    ]
    assert path.read_text() == original
