# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Deterministic local Flask server for Playwright/Chromium browser tests.

Loads the frontend fixture JSON payloads and serves in-memory implementations of
the shared API contracts on a random local port. No real Home Assistant host is
ever contacted.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Thread

import pytest
from flask import Flask, Response, jsonify, request
from werkzeug.serving import make_server


def _load_fixture(repo_root: Path, name: str) -> dict:
    path = repo_root / "tests" / "fixtures" / name
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def browser_error_gate(page) -> None:
    """Fail any browser test that raised a console error or warning, an uncaught
    page error, or a failed network request.

    Accepting the whole frontend means the console must stay clean across the
    entire walk — not just inside individual feature tests — so this autouse
    gate runs on every page in every browser test, including the end-to-end
    acceptance round trip.
    """

    violations: list[str] = []

    def on_console(message) -> None:
        if message.type == "error":
            # Chromium reflects a non-2xx HTTP response as a console error
            # ("Failed to load resource: the server responded with a status of
            # 422"). The negative-path operations tests deliberately route such
            # error responses to prove secret-safe error rendering, so this
            # browser-network reflection is not an application error: the app
            # never logs it and the response+status are asserted by the test.
            if message.text.startswith("Failed to load resource:"):
                return
            violations.append(f"console error: {message.text}")
        elif message.type == "warning":
            violations.append(f"console warning: {message.text}")

    def on_pageerror(error) -> None:
        violations.append(f"pageerror: {error}")

    def on_requestfailed(request) -> None:
        violations.append(f"request failed: {request.url}")

    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    page.on("requestfailed", on_requestfailed)
    yield
    assert violations == [], "console/page-error/request gate failed:\n" + "\n".join(violations)


@pytest.fixture
def captured_requests() -> list[dict]:
    """POST request bodies recorded against the in-memory browser server."""
    return []


@pytest.fixture
def app_url(captured_requests: list[dict], repo_root):
    """Serve the frontend shell and fake API, yielding its base URL."""
    config_payload = _load_fixture(repo_root, "frontend_config.json")
    meta_payload = _load_fixture(repo_root, "frontend_meta.json")
    # Recorded output of the real firmware core (tools/micropad_sim.cpp run over
    # the config fixture's first page), so the preview tests draw a genuine prim
    # model instead of a hand-written one.
    panel_payload = _load_fixture(repo_root, "frontend_panel.json")

    package_root = repo_root / "src" / "micropad"
    app = Flask(
        __name__,
        static_folder=str(package_root / "static"),
        template_folder=str(package_root / "templates"),
    )

    def record() -> None:
        captured_requests.append({"path": request.path, "json": request.get_json(silent=True) or {}})

    @app.get("/")
    def index() -> Response:
        return app.make_response(
            app.jinja_env.get_template("index.html").render(asset_version="1")
        )

    @app.get("/api/meta")
    def get_meta() -> Response:
        return jsonify(meta_payload)

    @app.get("/api/config")
    def get_config() -> Response:
        return jsonify(config_payload)

    @app.post("/api/config")
    def post_config() -> Response:
        record()
        config_payload.clear()
        config_payload.update(captured_requests[-1]["json"])
        return jsonify(config_payload)

    @app.post("/api/validate")
    def validate() -> Response:
        record()
        time.sleep(0.1)  # keep the operation busy long enough to observe the disabled state
        return jsonify({"valid": True})

    @app.get("/api/generate")
    def generate() -> Response:
        return jsonify({"yaml": "alias: MicroPad Controller\n", "filename": "micropad_controller.yaml"})

    @app.get("/api/history")
    def history_list() -> Response:
        return jsonify(
            {
                "snapshots": [
                    {"id": "20260915T090000Z-aaaaaaaa", "created_at": "2026-09-15T09:00:00+00:00",
                     "pages": 4, "items": 7},
                    {"id": "20260915T080000Z-bbbbbbbb", "created_at": "2026-09-15T08:00:00+00:00",
                     "pages": 4, "items": 6},
                ],
                "limit": 20,
            }
        )

    @app.get("/api/history/<snapshot_id>")
    def history_show(snapshot_id: str) -> Response:
        return jsonify(
            {
                "snapshot": snapshot_id,
                "config": config_payload,
                "diff": ['page "home" title: \'Home\' -> \'Home v2\'', "global keymap r0c0: home -> back"],
            }
        )

    @app.post("/api/history/<snapshot_id>/restore")
    def history_restore(snapshot_id: str) -> Response:
        record()
        restored = json.loads(json.dumps(config_payload))
        restored["pages"][0]["title"] = "Restored home"
        config_payload.clear()
        config_payload.update(restored)
        return jsonify(
            {"ok": True, "snapshot": snapshot_id,
             "applied": ['page "home" title: \'Home\' -> \'Restored home\''], "config": restored}
        )

    @app.get("/api/entity-groups")
    def entity_groups() -> Response:
        # Deliberately not recorded: mock instrumentation feeds captured_requests,
        # and the existing write-count assertions ("no write happened") would count
        # this read. The button's presence in the UI is the proof it was fetched.
        # Built with the real grouping helper so the mock cannot invent a shape the
        # backend never produces.
        from micropad.page_builder import group_counts

        cache = config_payload.get("entity_cache") or []
        return jsonify(
            {
                "groups": group_counts(cache),
                "pages": len(config_payload.get("pages") or []),
                "limits": {"max_pages": meta_payload["caps"]["max_pages"],
                           "max_items_per_page": meta_payload["caps"]["max_items_per_page"]},
            }
        )

    @app.post("/api/build-pages")
    def build_pages_route() -> Response:
        from micropad.page_builder import build_pages

        record()
        body = request.get_json(silent=True) or {}
        domains = body.get("domains") if isinstance(body.get("domains"), list) else None
        cache = config_payload.get("entity_cache") or []
        generated = build_pages(
            cache,
            domains=domains,
            existing_page_ids=[page.get("page_id") for page in config_payload.get("pages") or []],
            existing_page_count=len(config_payload.get("pages") or []),
        )
        return jsonify(generated.as_dict())

    @app.post("/api/lint")
    def lint() -> Response:
        record()
        config = request.get_json(silent=True) or {}
        pages = config.get("pages") or []
        page_bytes = {
            page.get("page_id", f"#{index}"): 500 + 40 * index for index, page in enumerate(pages)
        }
        return jsonify(
            {
                "ok": True,
                "findings": [],
                "page_bytes": page_bytes,
                "catalog_bytes": 1300,
                "keymap_bytes": 700,
                "limits": {"page_bytes": 8192, "catalog_bytes": 16000, "keymap_bytes": 16380,
                           "mqtt_buffer_bytes": 16384},
                "caps": {"field_caps": meta_payload["caps"]["field_caps"],
                         "max_pages": meta_payload["caps"]["max_pages"],
                         "max_items_per_page": meta_payload["caps"]["max_items_per_page"]},
            }
        )

    @app.post("/api/simulate")
    def simulate() -> Response:
        record()
        return jsonify(panel_payload["response"])

    @app.get("/api/load-current-page")
    def load_current_page() -> Response:
        return jsonify({"page_id": request.args.get("page_id", "home"), "items": []})

    @app.post("/api/ha/test")
    def test_ha() -> Response:
        record()
        return jsonify({"ok": True, "message": "Home Assistant connection succeeded"})

    @app.get("/api/ha/entities")
    def ha_entities() -> Response:
        return jsonify(config_payload["entity_cache"])

    @app.post("/api/upload/api")
    def upload_api() -> Response:
        record()
        return jsonify({"ok": True, "message": "Home Assistant upload verified."})

    @app.post("/api/upload/ssh")
    def upload_ssh() -> Response:
        record()
        return jsonify({"ok": True, "message": "SSH upload verified."})

    @app.get("/api/download/api")
    def download_api() -> Response:
        return Response(
            "alias: MicroPad Controller\n",
            mimetype="application/yaml",
            headers={"Content-Disposition": "attachment; filename=micropad_controller.yaml"},
        )

    @app.get("/api/download/ssh")
    def download_ssh() -> Response:
        return Response(
            "#!/bin/sh\n",
            mimetype="text/x-shellscript",
            headers={"Content-Disposition": "attachment; filename=upload_micropad.sh"},
        )

    server = make_server("127.0.0.1", 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)