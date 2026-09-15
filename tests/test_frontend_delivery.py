# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Delivery contract for the static frontend served by the MicroPad backend."""

from __future__ import annotations

import re

NOTICE = "AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications."


def test_index_is_uncached_and_assets_are_versioned(client):
    response = client.get("/")
    html = response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-cache"
    assert '/static/css/app.css?v=' in html
    assert '/static/js/app.js?v=' in html
    assert 'lang="en"' in html


def test_versioned_static_assets_are_immutable(client):
    response = client.get("/static/js/app.js?v=test-version")
    assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_immutable_cache_policy_does_not_leak_onto_api_or_unversioned_assets(client):
    # API responses must stay no-store; unversioned static must stay uncacheable.
    assert "immutable" not in client.get("/api/config").headers.get("Cache-Control", "")
    assert "immutable" not in client.get("/static/js/app.js").headers.get("Cache-Control", "")
    # The versioned asset URLs embedded in the page must be stable across loads and carry a value.
    first = client.get("/").get_data(as_text=True)
    second = client.get("/").get_data(as_text=True)
    versions = re.findall(r'/static/(?:css/app.css|js/app.js)\?v=([^"&]+)', first)
    assert len(versions) == 2 and len(set(versions)) == 1
    assert versions == re.findall(r'/static/(?:css/app.css|js/app.js)\?v=([^"&]+)', second)


def test_frontend_sources_carry_notice(repo_root):
    paths = [repo_root / "src/micropad/templates/index.html", *sorted((repo_root / "src/micropad/static").rglob("*.js")), *sorted((repo_root / "src/micropad/static").rglob("*.css"))]
    assert paths
    assert all(NOTICE in path.read_text(encoding="utf-8") for path in paths)


def test_browser_fixture_mirrors_the_real_meta_payload(client, repo_root):
    """The browser test fixture stands in for /api/meta, so it must not drift.

    The editor derives its item-type rules (entity required, target page required)
    from these descriptors, so a stale fixture would let the browser suite pass
    against rules the real server no longer has.
    """
    import json

    fixture = json.loads((repo_root / "tests/fixtures/frontend_meta.json").read_text(encoding="utf-8"))
    live = client.get("/api/meta").get_json()
    for key in ("mqtt_contract_version", "home_page_id", "key_ids", "item_types",
                "item_type_meta", "limits", "caps", "automation_id"):
        assert fixture[key] == live[key], f"fixture key {key!r} drifted from /api/meta"
    assert {item["id"] for item in fixture["actions"]} == {item["id"] for item in live["actions"]}