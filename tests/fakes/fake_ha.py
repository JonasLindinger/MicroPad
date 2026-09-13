# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Thread-backed fake Home Assistant REST server for real-HTTP round trips.

This fake implements only the endpoints the real ``HomeAssistantClient`` and
``HomeAssistantDeployer`` exercise, backed by a live local HTTP server, so tests
prove a genuine create/update HTTP round trip without any live Home Assistant.

Every endpoint returns an independently configurable status code (via
:attr:`status_overrides`) and records every request in request order (via
:attr:`request_log`), so fail-closed release-script gates can be asserted to
make zero network calls.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Thread

from flask import Flask, jsonify, request
from werkzeug.serving import BaseWSGIServer, make_server

_FAILURE_KEYS = frozenset(
    {"api_root", "states", "get_automation", "put_automation", "reload", "publish"}
)


@dataclass(frozen=True)
class ServiceCall:
    domain: str
    service: str
    data: dict[str, object]


def _status(overrides: dict[str, int], key: str, default: int) -> int:
    """Resolve a per-route status override, validating the key at configuration time."""
    return int(overrides.get(key, default))


class FakeHomeAssistant:
    """In-process HTTP server recording automation writes and service calls."""

    def __init__(self) -> None:
        self.automation: dict[str, object] | None = None
        self.states: list[dict[str, object]] = []
        self.service_calls: list[ServiceCall] = []
        self.automation_reads = 0
        self.automation_writes = 0
        self.reload_count = 0
        self.mqtt_calls: list[dict[str, object]] = []
        self.request_log: list[tuple[str, str]] = []
        self.status_overrides: dict[str, int] = {}
        self.read_back_transform: Callable[[dict[str, object]], dict[str, object]] = lambda value: (
            value
        )
        self._server: BaseWSGIServer | None = None
        self._thread: Thread | None = None
        self.base_url = ""
        self.url = ""

    # --- SELFTEST guard so addons cannot fat-finger the route keys ---
    def _apply_override(self, key: str, *, value: int | None, mode: str) -> None:
        if value is None:
            return
        if key not in _FAILURE_KEYS:
            raise ValueError(f"unknown fake-HA route key: {key}")
        if mode == "set":
            self.status_overrides[key] = value

    def set_status(self, key: str, value: int) -> FakeHomeAssistant:
        """Configure one endpoint to return ``value``; raises on unknown route key."""
        self._apply_override(key, value=value, mode="set")
        return self

    def read_automation(self, automation_id: str) -> dict[str, object] | None:
        """Return the stored automation dict or ``None`` when absent / wrong ID."""
        if self.automation is None or automation_id != "micropad_controller":
            return None
        return self.automation

    def __enter__(self) -> FakeHomeAssistant:
        app = Flask("fake-home-assistant")

        @app.before_request
        def authenticate() -> tuple[object, int] | None:
            self.request_log.append((request.method, request.path))
            authorization = request.headers.get("Authorization", "")
            if not authorization.startswith("Bearer ") or not authorization[len("Bearer ") :].strip():
                return jsonify({"message": "unauthorized"}), 401
            return None

        @app.get("/api/")
        def api_root() -> tuple[object, int]:
            status = _status(self.status_overrides, "api_root", 200)
            return jsonify({"message": "API running."}), status

        @app.get("/api/states")
        def states() -> tuple[object, int]:
            status = _status(self.status_overrides, "states", 200)
            if status >= 400:
                return jsonify({"message": "failed"}), status
            return jsonify(self.states), status

        @app.get("/api/config/automation/config/<automation_id>")
        def get_automation(automation_id: str) -> tuple[object, int]:
            self.automation_reads += 1
            status = _status(self.status_overrides, "get_automation", 200)
            if automation_id != "micropad_controller" or self.automation is None:
                return jsonify({"message": "not found"}), 404
            if status >= 400:
                return jsonify({"message": "failed"}), status
            return jsonify(self.read_back_transform(self.automation)), 200

        @app.post("/api/config/automation/config/<automation_id>")
        def put_automation(automation_id: str) -> tuple[object, int]:
            self.automation_writes += 1
            status = _status(self.status_overrides, "put_automation", 200)
            if automation_id != "micropad_controller":
                return jsonify({"message": "wrong automation id"}), 404
            self.automation = dict(request.get_json())
            if status >= 400:
                return jsonify({"message": "failed"}), status
            return jsonify({"result": "ok"}), 200

        @app.post("/api/services/<domain>/<service>")
        def service(domain: str, service: str) -> tuple[object, int]:
            data = dict(request.get_json())
            self.service_calls.append(ServiceCall(domain, service, data))
            if (domain, service) == ("automation", "reload"):
                self.reload_count += 1
                key = "reload"
            elif (domain, service) == ("mqtt", "publish"):
                self.mqtt_calls.append(
                    {
                        "topic": data.get("topic", ""),
                        "payload": data.get("payload", ""),
                        "retain": data.get("retain", False),
                        "qos": data.get("qos", 0),
                    }
                )
                key = "publish"
            else:
                key = ""  # only known routes may carry an override; others default 200
            status = _status(self.status_overrides, key, 200) if key else 200
            if status >= 400:
                return jsonify({"message": "failed"}), status
            return jsonify([]), 200

        self._server = make_server("127.0.0.1", 0, app, threaded=True)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"
        self.url = self.base_url
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        assert self._server is not None and self._thread is not None
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()