# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Real Flask + Chromium test that the frontend authenticates (P0.1/P1.12).

Unlike the fake-server browser harness, this starts the real ``create_app`` with a
configured admin secret and drives a real browser through: anonymous access is
denied (401), the sign-in dialog appears, entering the secret re-loads the app,
and the dialog disappears. ``sessionStorage`` is the only storage used.
"""

from __future__ import annotations

from pathlib import Path
from threading import Thread

import pytest
from werkzeug.serving import make_server

from micropad.app import create_app
from micropad.config_store import ConfigStore
from micropad.models import default_config

ADMIN = "topsecret"


@pytest.fixture
def auth_url(tmp_path: Path):
    example = tmp_path / "config.example.json"
    example.write_text(default_config().model_dump_json(by_alias=True), encoding="utf-8")
    store = ConfigStore(tmp_path / "config.json", example)
    app = create_app(store.path, admin_secret=ADMIN)
    server = make_server("127.0.0.1", 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_anonymous_is_denied_and_sign_in_dialog_appears(page, auth_url: str) -> None:
    page.goto(auth_url + "/")
    dialog = page.locator("#auth-dialog")
    page.wait_for_selector("#auth-dialog:not([hidden])", timeout=5000)
    assert dialog.is_visible()


def test_entering_the_admin_secret_loads_the_app_and_hides_the_dialog(
    page, auth_url: str
) -> None:
    page.goto(auth_url + "/")
    page.wait_for_selector("#auth-dialog:not([hidden])", timeout=5000)
    page.fill("#auth-secret", "topsecret")
    page.click("#auth-form button[type='submit']")
    # The app mounts the page tree once the authenticated config loads.
    page.wait_for_selector("#page-tree", timeout=8000)
    assert page.evaluate("document.getElementById('auth-dialog').hidden") is True


def test_secret_is_kept_in_session_storage_only(page, auth_url: str) -> None:
    page.goto(auth_url + "/")
    page.wait_for_selector("#auth-dialog:not([hidden])", timeout=5000)
    page.fill("#auth-secret", "topsecret")
    page.click("#auth-form button[type='submit']")
    page.wait_for_selector("#page-tree", timeout=8000)
    session_keys = page.evaluate("Object.keys(sessionStorage)")
    assert any("admin" in key for key in session_keys)
    local_keys = page.evaluate("Object.keys(localStorage)")
    assert not any("admin" in key for key in local_keys)
    # The secret must never appear in the URL or any rendered text.
    assert "topsecret" not in page.url
    assert "topsecret" not in page.locator("body").inner_text()