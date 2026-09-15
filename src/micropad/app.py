# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Flask application factory for the MicroPad configuration and generation API."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from flask import Flask, Response, jsonify, render_template, request
from pydantic import ValidationError
from werkzeug.exceptions import BadRequest, MethodNotAllowed, NotFound

from micropad import history, simulator
from micropad.config_store import ConfigStore
from micropad.constants import (
    AUTOMATION_ID,
    CAPS,
    FIELD_CAPS,
    ITEM_TYPES,
    LIMITS,
    MAX_ITEMS_PER_PAGE,
    MAX_PAGES,
)
from micropad.generator import (
    GenerationError,
    generate_bundle,
    generate_page_payload,
    load_contract,
)
from micropad.ha_client import HAClientError, HomeAssistantClient
from micropad.ha_deploy import (
    HADeploymentError,
    HADeploymentPartialError,
    HomeAssistantDeployer,
    MqttVerifier,
)
from micropad.lint import analyze
from micropad.models import (
    AppConfig,
    Settings,
    default_keymap,
    parse_config,
    public_config,
)
from micropad.page_builder import build_pages, group_counts
from micropad.security import (
    CSRF_HEADER,
    CSRF_VALUE,
    UNSAFE_METHODS,
    allow_unauthenticated_lan,
    bearer_token,
    constant_time_equal,
    is_loopback,
)
from micropad.security import (
    admin_secret as env_admin_secret,
)
from micropad.ssh_upload import SSHDeployer, SSHUploadError, downloadable_ssh_script
from micropad.ui_meta import action_metadata, item_type_metadata, page_templates


def _asset_version() -> str:
    """Content hash of the served frontend bundle.

    The version is part of an *immutable* asset URL (``max-age=31536000``), so
    it has to change whenever a stylesheet or module changes. A constant
    version instead pins the first copy a browser ever saw for a year: after a
    deploy the page keeps the old look, which reads as "the CSS is missing".
    """
    digest = hashlib.sha256()
    static_root = Path(__file__).with_name("static")
    for path in sorted(
        candidate
        for candidate in static_root.rglob("*")
        if candidate.suffix in {".css", ".js"} and candidate.is_file()
    ):
        digest.update(path.relative_to(static_root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


ASSET_VERSION = _asset_version()


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
        # No secret configured: loopback clients only (dev posture) unless the operator
        # deliberately opened the API to a trusted LAN (MICROPAD_ALLOW_UNAUTHENTICATED_LAN).
        authorized = remote_is_loopback or allow_unauthenticated_lan()

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
    mqtt_verifier_factory: Callable[[Settings], MqttVerifier | None] | None = None,
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

    @app.errorhandler(HADeploymentPartialError)
    def ha_partial_error(error: HADeploymentPartialError) -> tuple[Response, int]:
        # P1.14: a partial deployment reports exactly what remains changed.
        return _error(
            "ha_partial_deployment",
            "Home Assistant deployment was only partially applied",
            502,
            list(error.remaining),
        )

    @app.errorhandler(SSHUploadError)
    def ssh_error(error: SSHUploadError) -> tuple[Response, int]:
        return _error("ssh_error", "SSH deployment failed", 502)

    @app.get("/healthz")
    def healthz() -> Response:
        return jsonify({"ok": True, "service": "micropad-configurator"})

    def normalize_incoming_document(raw: object) -> dict[str, object]:
        """Undo the display-only redaction that ``public_config`` applies.

        ``GET /api/config`` answers with empty secrets plus ``*_configured`` flags, and
        the editor legitimately echoes that document back (lint, validate, save). Handled
        only in the save path, the redaction flags reached the strict model through
        ``/api/lint`` -- so the analysis reported our own API response as "not publishable"
        and every byte budget stayed 0 for a perfectly valid configuration.
        """
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
        return incoming

    def parse_incoming_config() -> AppConfig:
        return parse_config(normalize_incoming_document(request.get_json(force=True)))

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
                # Descriptor per item type (HA domains, default action, whether the
                # editor must collect an entity or a target page) so the frontend
                # stops hard-coding the mapping the firmware already owns.
                "item_type_meta": item_type_metadata(),
                # Byte ceilings and device caps: the numbers the editor needs to
                # warn before the pad silently clips a value or the MQTT client
                # drops an oversized catalog.
                "limits": dict(LIMITS),
                "caps": {
                    "max_pages": CAPS["max_pages"],
                    "max_items_per_page": CAPS["max_items_per_page"],
                    "key_count": CAPS["key_count"],
                    "field_caps": dict(FIELD_CAPS),
                },
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

    @app.get("/api/history")
    def history_list() -> Response:
        """Recorded configuration revisions, newest first (B7).

        Snapshots are written by every save (see ``micropad.history``); this exposes
        only their metadata, never their content.
        """
        return jsonify(
            {
                "snapshots": [
                    snapshot.as_dict() for snapshot in history.list_snapshots(store.path)
                ],
                "limit": history.SNAPSHOT_LIMIT,
            }
        )

    @app.get("/api/history/<snapshot_id>")
    def history_show(snapshot_id: str) -> Response | tuple[Response, int]:
        """One revision: its configuration (redacted) plus what restoring it changes."""
        try:
            payload = history.load_snapshot(store.path, snapshot_id)
        except ValueError:
            return _error("unknown_snapshot", "No such configuration revision", 404)
        except (FileNotFoundError, json.JSONDecodeError):
            return _error("unknown_snapshot", "No such configuration revision", 404)
        try:
            restored = parse_config(payload)
        except ValidationError as error:
            # An old revision can predate a schema change: say so instead of 500.
            return _error("snapshot_invalid", f"That revision no longer validates: {error}", 422)
        current = store.load().model_dump(mode="json", by_alias=True)
        return jsonify(
            {
                "snapshot": snapshot_id,
                "config": public_config(restored),
                "diff": history.diff_configs(current, payload),
            }
        )

    @app.post("/api/history/<snapshot_id>/restore")
    def history_restore(snapshot_id: str) -> Response | tuple[Response, int]:
        """Restore a revision as the current configuration.

        The restore goes through the normal save path, so it is validated and redacted
        like any other save. Snapshots are content-addressed, so restoring a state that
        is already in the history adds no duplicate revision — the revision is still
        there, which is what makes an undo of an undo unnecessary.
        """
        try:
            payload = history.load_snapshot(store.path, snapshot_id)
        except ValueError:
            return _error("unknown_snapshot", "No such configuration revision", 404)
        except (FileNotFoundError, json.JSONDecodeError):
            return _error("unknown_snapshot", "No such configuration revision", 404)
        try:
            restored = parse_config(payload)
        except ValidationError as error:
            return _error("snapshot_invalid", f"That revision no longer validates: {error}", 422)
        current = store.load().model_dump(mode="json", by_alias=True)
        applied = history.diff_configs(current, payload)
        store.save(restored)
        return jsonify(
            {"ok": True, "snapshot": snapshot_id, "applied": applied,
             "config": public_config(restored)}
        )

    @app.get("/api/entity-groups")
    def entity_groups() -> Response:
        """Pageable domains in the entity cache, for the page generator's buttons.

        Reads the cache the configurator already holds (refreshed through
        ``/api/ha/entities``), so rendering the picker costs no Home Assistant call
        and cannot fail while the operator is editing.
        """
        config = store.load()
        cache = [entity.model_dump(mode="json") for entity in config.entity_cache]
        return jsonify(
            {
                "groups": group_counts(cache),
                "pages": len(config.pages),
                "limits": {"max_pages": MAX_PAGES, "max_items_per_page": MAX_ITEMS_PER_PAGE},
            }
        )

    @app.post("/api/build-pages")
    def build_pages_route() -> Response:
        """Turn cached entities into page drafts (nothing is saved by this route).

        The operator sees the drafts in the UI, which merges them into the open
        configuration; saving stays an explicit action on ``/api/config``.
        """
        raw = request.get_json(force=True)
        if not isinstance(raw, dict):
            raise BadRequest("request body must be a JSON object")
        domains_raw = raw.get("domains")
        domains: list[str] | None = None
        if isinstance(domains_raw, list):
            domains = [str(domain) for domain in domains_raw]
        elif domains_raw is not None:
            raise BadRequest("domains must be a list of strings")
        config = store.load()
        cache = [entity.model_dump(mode="json") for entity in config.entity_cache]
        generated = build_pages(
            cache,
            domains=domains,
            existing_page_ids=[page.page_id for page in config.pages],
            existing_page_count=len(config.pages),
        )
        return jsonify(generated.as_dict())

    @app.post("/api/lint")
    def lint_config() -> Response:
        """Payload budgets and "what the device will change" for a candidate config.

        Accepts the same body as ``/api/validate`` (a full configuration document)
        so the editor can analyse an unsaved draft. Findings come from the contract
        caps; byte budgets come from the real generator, so a valid config reports
        the exact sizes the deployment will publish. The body is normalized first for
        the same reason the save path normalizes it: the editor analyses the document
        ``GET /api/config`` returned, which carries the redaction flags the strict
        model must not see.
        """
        raw = request.get_json(force=True)
        if not isinstance(raw, dict):
            raise BadRequest("request body must be a JSON object")
        return jsonify(analyze(normalize_incoming_document(raw)).as_dict())

    @app.post("/api/simulate")
    def simulate_page() -> Response | tuple[Response, int]:
        """Render one page (or the portal view) with the firmware core.

        Body: ``{"page": {...}, "state": {...}, "key_id": "r0c3", "portal": {...}}``.
        The page is accepted in draft form — the preview exists to show what a
        half-finished page looks like on the panel — while identifiers are passed
        through unclipped so the answer reports the pad's own rejection.
        """
        raw = request.get_json(force=True)
        if not isinstance(raw, dict):
            raise BadRequest("request body must be a JSON object")
        page_raw = raw.get("page", {})
        page = page_raw if isinstance(page_raw, dict) else {}
        state_raw = raw.get("state", {})
        state = state_raw if isinstance(state_raw, dict) else {}
        portal_raw = raw.get("portal")
        portal = portal_raw if isinstance(portal_raw, dict) else None
        key_id = raw.get("key_id")
        directives = simulator.page_directives(
            page,
            selected=int(state.get("selected", 0)),
            first_visible=int(state.get("first_visible", 0)),
            editing=bool(state.get("editing", False)),
            network=int(raw.get("network", 0)),
            usb_host=bool(raw.get("usb_host", False)),
            portal=portal,
            keys=(raw.get("keys") or ()),
            press=str(key_id) if key_id else None,
        )
        try:
            result = simulator.simulate(directives)
        except simulator.SimulatorUnavailable as error:
            # Fail loudly instead of inventing a preview: the caller must know the
            # panel model is unavailable rather than trust a fake one.
            return _error("simulator_unavailable", str(error), 503)
        except simulator.SimulatorError as error:
            # The simulator refused the input itself (unknown item type, too many
            # items, malformed directive): that is a client-side problem, and the
            # message names the offending field.
            return _error("invalid_simulation_input", str(error), 400)
        # The tool reports the firmware build it was compiled from, so the UI
        # can say which core produced the preview.
        return jsonify(result)

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
        verifier = mqtt_verifier_factory(config.settings) if mqtt_verifier_factory else None
        result = HomeAssistantDeployer(ha_client_factory(config.settings)).deploy(
            config, mqtt_verifier=verifier
        )
        # P1.14: never claim "verified" before the retained topics were read back.
        if result.verified:
            message = "Home Assistant upload verified (automation read-back + retained topics)."
        else:
            unverified = ", ".join(result.unverified_topics) or "none"
            message = (
                "Automation read-back verified; retained MQTT topics were published but "
                f"NOT read back: {unverified}"
            )
        return jsonify(
            {
                "ok": True,
                "created": result.created,
                "automation_id": result.automation_id,
                "published_topics": list(result.published_topics),
                "verified": result.verified,
                "unverified_topics": list(result.unverified_topics),
                "message": message,
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
