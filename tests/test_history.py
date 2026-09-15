# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Configuration history, diff and restore (B7).

A bad edit must be a restore, not a re-typing session — and the diff must never leak
the secrets the snapshots contain. These tests cover the module, the three routes and
the two failure modes that matter (an unknown revision, a revision that no longer
validates).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from micropad import history
from micropad.config_store import ConfigStore


def _payload(bump: int = 0, *, credential: str = "first-credential") -> dict:
    return {
        "schema_version": 1,
        "settings": {
            "ha_url": "http://homeassistant.local:8123",
            "ha_token": credential,
            "ssh_host": "",
            "ssh_user": "",
            "ssh_key": "",
            "reload_strategy": "none",
        },
        "pages": [
            {
                "page_id": "home",
                "title": f"Home {bump}",
                "parent": "",
                "items": [
                    {"name": f"Lamp {index}", "type": "light", "entity": f"light.desk_{index}",
                     "state": "", "value": 0, "min": 0, "max": 100, "step": 1, "unit": "",
                     "editable": False, "target_page": ""}
                    for index in range(1 + bump)
                ],
                "keymap": {},
            }
        ],
        "entity_cache": [],
        "global_keymap": {},
    }


def test_record_is_content_addressed_and_bounded(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    first = history.record(path, _payload())
    assert first is not None
    # Same content again: nothing recorded (the UI autosaves on every edit).
    assert history.record(path, _payload()) is None
    second = history.record(path, _payload(1))
    assert second is not None and second != first

    for bump in range(2, history.SNAPSHOT_LIMIT + 5):
        history.record(path, _payload(bump))
    snapshots = history.list_snapshots(path)
    assert len(snapshots) == history.SNAPSHOT_LIMIT
    # Newest first, and the oldest entries were pruned.
    assert snapshots[0].snapshot_id > snapshots[-1].snapshot_id
    assert history.history_dir(path).is_dir()


def test_snapshot_ids_are_validated_before_any_file_is_opened(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    history.record(path, _payload())
    for bad in ("../../etc/passwd", "20260915T093012Z-1A2B3C4D", "whatever", "", "x/y"):
        with pytest.raises(ValueError):
            history.load_snapshot(path, bad)


def test_snapshots_are_written_with_owner_only_permissions(tmp_path: Path) -> None:
    import stat

    path = tmp_path / "config.json"
    history.record(path, _payload())
    snapshot = next(history.history_dir(path).glob("*.json"))
    mode = stat.S_IMODE(snapshot.stat().st_mode)
    # Same sensitivity as the configuration itself (it holds the HA token).
    assert mode == 0o600


def test_diff_is_structural_and_never_prints_secrets() -> None:
    older = _payload(0, credential="first-credential")
    newer = _payload(1, credential="second-credential")
    newer["settings"]["ha_url"] = "http://other.local:8123"
    lines = history.diff_configs(older, newer)
    assert any("title" in line for line in lines)
    assert any("items: 1 -> 2" in line for line in lines)
    assert "ha_token: changed" in lines
    assert any("setting ha_url" in line for line in lines)
    joined = "\n".join(lines)
    assert "first-credential" not in joined and "second-credential" not in joined


def test_diff_reports_page_and_keymap_changes() -> None:
    older = _payload()
    newer = _payload()
    newer["pages"].append({"page_id": "office", "title": "Office", "parent": "home",
                           "items": [], "keymap": {}})
    newer["global_keymap"] = {"r0c0": {"action": "navigate", "entity": "", "target_page": "office"}}
    lines = history.diff_configs(older, newer)
    assert 'page "office" added (0 items)' in lines
    assert any("global keymap r0c0" in line and "navigate(office)" in line for line in lines)

    removed = history.diff_configs(newer, older)
    assert 'page "office" removed' in removed
    # Identical documents say so rather than returning an empty list.
    assert history.diff_configs(older, older) == ["no differences"]


def test_store_save_records_a_revision(store: ConfigStore) -> None:
    # Uses the real configuration model (the fixture's default config): the history
    # module itself works on raw documents, but a save needs a valid AppConfig.
    config = store.load()
    store.save(config.model_copy(update={"pages": config.pages}))
    snapshots = history.list_snapshots(store.path)
    assert len(snapshots) == 1
    assert snapshots[0].pages == len(config.pages)
    # Saving the same document again records nothing new.
    store.save(store.load())
    assert len(history.list_snapshots(store.path)) == 1


def test_history_routes_list_show_and_restore(client, store) -> None:
    first = json.loads(json.dumps(client.get("/api/config").get_json()))
    first["pages"][0]["title"] = "Renamed home"
    assert client.post("/api/config", json=first).status_code == 200
    second = json.loads(json.dumps(first))
    second["pages"][0]["title"] = "Renamed again"
    assert client.post("/api/config", json=second).status_code == 200

    listing = client.get("/api/history").get_json()
    assert listing["limit"] == history.SNAPSHOT_LIMIT
    ids = [snapshot["id"] for snapshot in listing["snapshots"]]
    assert len(ids) == 2, listing
    # Metadata only: the listing never carries configuration content.
    assert "config" not in listing["snapshots"][0]
    assert set(listing["snapshots"][0]) == {"id", "created_at", "pages", "items"}

    older = ids[-1]
    shown = client.get(f"/api/history/{older}").get_json()
    assert shown["diff"], shown
    assert any("Renamed" in line for line in shown["diff"])
    # Public-shaped: the secret field is blanked and reported as a boolean.
    settings = shown["config"]["settings"]
    assert settings["ha_token"] == ""
    assert settings["ha_token_configured"] is False

    restored = client.post(f"/api/history/{older}/restore")
    assert restored.status_code == 200
    body = restored.get_json()
    assert body["ok"] is True and body["applied"]
    assert client.get("/api/config").get_json()["pages"][0]["title"] == "Renamed home"
    # Content-addressed history: that state is already recorded, so the restore adds
    # no duplicate revision.
    assert [s["id"] for s in client.get("/api/history").get_json()["snapshots"]] == ids


def test_history_routes_reject_an_unknown_revision(client) -> None:
    for method, path in (("get", "/api/history/nope"), ("post", "/api/history/nope/restore")):
        response = getattr(client, method)(path)
        assert response.status_code == 404
        assert response.get_json()["error"]["code"] == "unknown_snapshot"


def test_history_show_reports_a_revision_that_no_longer_validates(client, store) -> None:
    # A revision from before a schema change: it exists, it is readable, and it is
    # refused with a structured error rather than a traceback.
    store.path.parent.mkdir(parents=True, exist_ok=True)
    directory = history.history_dir(store.path)
    directory.mkdir(parents=True, exist_ok=True)
    stale = directory / "20260101T000000Z-deadbeef.json"
    stale.write_text(json.dumps({"pages": [{"page_id": "home"}]}), encoding="utf-8")
    response = client.get("/api/history/20260101T000000Z-deadbeef")
    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "snapshot_invalid"


@pytest.mark.parametrize("path", ["/api/history/..%2F..%2Fetc%2Fpasswd"])
def test_history_route_refuses_path_traversal(client, path: str) -> None:
    response = client.get(path)
    assert response.status_code in {400, 404}
