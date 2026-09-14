# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Flask application factory for the MicroPad configuration and generation API."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from flask import Flask, Response, jsonify, render_template, request
from pydantic import ValidationError
from werkzeug.exceptions import BadRequest, MethodNotAllowed, NotFound

from micropad.config_store import ConfigStore
from micropad.constants import AUTOMATION_ID, ITEM_TYPES
from micropad.generator import (
    GenerationError,
    generate_bundle,
    generate_page_payload,
    load_contract,
)
from micropad.ha_client import HAClientError, HomeAssistantClient
from micropad.ha_deploy import HADeploymentError, HomeAssistantDeployer
from micropad.models import (
    AppConfig,
    Settings,
    default_keymap,
    parse_config,
    public_config,
)
from micropad.security import (
    CSRF_HEADER,
    CSRF_VALUE,
    UNSAFE_METHODS,
    bearer_token,
    constant_time_equal,
    is_loopback,
)
from micropad.security import (
    admin_secret as env_admin_secret,
)
from micropad.ssh_upload import SSHDeployer, SSHUploadError, downloadable_ssh_script
from micropad.ui_meta import action_metadata, page_templates

ASSET_VERSION = "1"


def _error(
    code: str, message: str, status: int, details: Sequence[object] | None = None
) -> tuple[Response, int]:
    return jsonify(
        {"ok": False, "error": {"code": code, "message": message, "details": details or []}}
    ), status


def _requires_auth(path: str) -> bool:
    """Only sensitive API routes require authentication.

    ``/healthz`` stays anonymous by contract and returns no sensitive data.
    ``/api/meta`` exposes public protocol metadata only.  The index page and
    static assets are inert.
    """
    if path == "/healthz" or path == "/api/meta":
        return False
    if path == "/" or path.startswith("/static/"):
        return False
    return path.startswith("/api/")


def _authorize(secret: str | None) -> tuple[Response, int] | None:
    """Fail-closed gate for sensitive routes (see ``security.py``)."""
    remote_is_loopback = is_loopback(request.remote_addr)
    authorized = False
    if secret:
        authorized = constant_time_equal(
            bearer_token(request.headers.get("Authorization")), secret
        )
    else:
        # No secret configured: allow only genuine loopback clients (dev posture).
        authorized = remote_is_loopback

    if not authorized:
        return _error("unauthorized", "Administrator authentication required", 401)

    # Defense in depth against cross-site requests writing state.  A cross-site
    # HTML form cannot send a custom header without a CORS preflight, which we
    # never grant; requiring it here stops any write that was not issued by the
    # micro-pad origin itself.  Loopback clients (same host, local dev) are exempt.
    if request.method in UNSAFE_METHODS and not remote_is_loopback:
        if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            return _error("csrf_required", "Missing cross-site request guard", 403)
    return None


def create_app(
    config_path: Path | None = None,
    *,
    ha_client_factory: Callable[[Settings], HomeAssistantClient] = HomeAssistantClient,
    ssh_deployer_factory: Callable[[Settings], SSHDeployer] = SSHDeployer,
    admin_secret: str | None = None,
) -> Flask:
    """Build the configurator application with a validated config store.

    ``admin_secret`` overrides the ``MICROPAD_ADMIN_SECRET`` environment
    variable (used by tests and the process entry point)."""
    app = Flask(__name__)
    if admin_secret is None:
        admin_secret = env_admin_secret()
    app.extensions["micropad_admin_secret"] = admin_secret

    app.before_request(
        lambda: _authorize(admin_secret) if _requires_auth(request.path) else None
    )
    project_root = Path.cwd()
    resolved_config = config_path or Path(
        os.environ.get(
            "MICROPAD_CONFIG_PATH",
            os.environ.get("MICROPAD_CONFIG", str(project_root / "config.json")),
        )
    )
    example_path = Path(
        os.environ.get("MICROPAD_CONFIG_EXAMPLE", str(project_root / "config.example.json"))
    )
    store = ConfigStore(resolved_config, example_path)
    app.extensions["micropad_store"] = store

    @app.after_request
    def api_cache_headers(response: Response) -> Response:
        if request.path.startswith("/static/") and request.args.get("v"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif request.path == "/":
            response.headers["Cache-Control"] = "no-cache"
        elif request.path.startswith("/api/") or request.path == "/healthz":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index() -> Response:
        response = app.make_response(render_template("index.html", asset_version=ASSET_VERSION))
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.errorhandler(ValidationError)
    def validation_error(error: ValidationError) -> tuple[Response, int]:
        details = [
            {"path": ".".join(map(str, item["loc"])), "message": item["msg"]}
            for item in error.errors(include_url=False, include_input=False)
        ]
        return _error("validation_error", "Configuration is invalid", 422, details)

    @app.errorhandler(GenerationError)
    def generation_error(error: GenerationError) -> tuple[Response, int]:
        return _error("generation_error", str(error), 422)

    @app.errorhandler(BadRequest)
    def bad_request(error: BadRequest) -> tuple[Response, int]:
        return _error("bad_request", "Request body must contain valid JSON", 400)

    @app.errorhandler(NotFound)
    def not_found(error: NotFound) -> tuple[Response, int]:
        return _error("not_found", "API route was not found", 404)

    @app.errorhandler(MethodNotAllowed)
    def method_not_allowed(error: MethodNotAllowed) -> tuple[Response, int]:
        return _error("method_not_allowed", "HTTP method is not allowed for this route", 405)

    @app.errorhandler(HAClientError)
    def ha_error(error: HAClientError) -> tuple[Response, int]:
        return _error("ha_error", "Home Assistant request failed", 502)

    @app.errorhandler(HADeploymentError)
    def ha_verification_error(error: HADeploymentError) -> tuple[Response, int]:
        return _error("ha_verification_error", "Home Assistant deployment verification failed", 502)

    @app.errorhandler(SSHUploadError)
    def ssh_error(error: SSHUploadError) -> tuple[Response, int]:
        return _error("ssh_error", "SSH deployment failed", 502)

    @app.get("/healthz")
    def healthz() -> Response:
        return jsonify({"ok": True, "service": "micropad-configurator"})

    def parse_incoming_config() -> AppConfig:
        raw = request.get_json(force=True)
        if not isinstance(raw, dict):
            # P1.13: a non-object top-level body is a structured JSON 400, never
            # an HTML 500 from dict() on an int/list/None.
            raise BadRequest("request body must be a JSON object")
        incoming: dict[str, object] = dict(raw)
        current = store.load()
        settings_raw = incoming.get("settings", {})
        if isinstance(settings_raw, dict):
            settings: dict[str, object] = dict(settings_raw)
            settings.pop("ha_token_configured", None)
            settings.pop("ssh_key_configured", None)
            if not settings.get("ha_token"):
                settings["ha_token"] = current.settings.ha_token
            if not settings.get("ssh_key"):
                settings["ssh_key"] = current.settings.ssh_key
            incoming["settings"] = settings
        else:
            # Leave a non-dict settings value untouched so the model rejects it
            # with a structured 422 validation error.
            incoming["settings"] = settings_raw
        # P1.11: entity_cache is server-managed (filled by /api/ha/entities). A
        # stale UI config that omits or empties it must never wipe the cache, so
        # preserve the stored cache unless the client genuinely supplies a fresh
        # non-empty list.
        cache_raw = incoming.get("entity_cache")
        if not isinstance(cache_raw, list) or not cache_raw:
            incoming["entity_cache"] = current.entity_cache
        return parse_config(incoming)

    @app.get("/api/config")
    def get_config() -> Response:
        return jsonify(public_config(store.load()))

    @app.post("/api/config")
    def post_config() -> Response:
        saved = parse_incoming_config()
        generate_bundle(saved)
        store.save(saved)
        return jsonify(public_config(saved))

    @app.get("/api/meta")
    def get_meta() -> Response:
        contract = load_contract()
        return jsonify(
            {
                "mqtt_contract_version": contract["version"],
                "home_page_id": "home",
                "key_ids": list(cast(list[object], contract["key_ids"])),
                "actions": action_metadata(),
                "item_types": sorted(ITEM_TYPES),
                "default_keymap": {
                    key: value.model_dump(mode="json") for key, value in default_keymap().items()
                },
                "templates": page_templates(),
                "automation_id": AUTOMATION_ID,
            }
        )

    @app.post("/api/validate")
    def validate_config() -> Response:
        candidate = parse_incoming_config()
        generate_bundle(candidate)
        return jsonify({"ok": True, "config": public_config(candidate)})

    @app.get("/api/generate")
    def generate() -> Response:
        bundle = generate_bundle(store.load())
        return jsonify(
            {
                "automation_yaml": bundle.yaml_text,
                "filename": "micropad_controller.yaml",
                "mqtt": {
                    "micropad/pages/all": bundle.catalog_payload,
                    "micropad/page/current": bundle.home_payload,
                    "micropad/keymap": bundle.home_keymap_payload,
                },
            }
        )

    @app.get("/api/load-current-page")
    def load_current_page() -> Response:
        config = store.load()
        page_id = request.args.get("page_id", "home")
        return jsonify(generate_page_payload(config, str(page_id)))

    @app.get("/api/download/api")
    def download_api() -> Response:
        text = generate_bundle(store.load()).yaml_text
        return Response(
            text,
            mimetype="application/yaml",
            headers={"Content-Disposition": "attachment; filename=micropad_controller.yaml"},
        )

    @app.post("/api/ha/test")
    def test_ha() -> Response:
        result = ha_client_factory(store.load().settings).test_auth()
        return jsonify(
            {
                "ok": True,
                "message": str(result.get("message", "Home Assistant connection succeeded")),
            }
        )

    @app.get("/api/ha/entities")
    def get_entities() -> Response:
        entities = ha_client_factory(store.load().settings).list_entities()
        store.update(lambda config: config.model_copy(update={"entity_cache": entities}))
        return jsonify([entity.model_dump(mode="json") for entity in entities])

    @app.post("/api/upload/api")
    def upload_api() -> Response:
        config = store.load()
        result = HomeAssistantDeployer(ha_client_factory(config.settings)).deploy(config)
        return jsonify(
            {
                "ok": True,
                "created": result.created,
                "automation_id": result.automation_id,
                "published_topics": list(result.published_topics),
                "message": "Home Assistant upload verified.",
            }
        )

    @app.post("/api/upload/ssh")
    def upload_ssh() -> Response:
        config = store.load()
        yaml_text = generate_bundle(config).yaml_text
        remote_temp = ssh_deployer_factory(config.settings).deploy(yaml_text)
        return jsonify(
            {
                "ok": True,
                "remote_path": config.settings.remote_path,
                "temporary_path": remote_temp,
                "message": "SSH upload verified.",
            }
        )

    @app.get("/api/download/ssh")
    def download_ssh() -> Response:
        script = downloadable_ssh_script(store.load().settings)
        return Response(
            script,
            mimetype="text/x-shellscript",
            headers={"Content-Disposition": "attachment; filename=upload_micropad.sh"},
        )

    return app
