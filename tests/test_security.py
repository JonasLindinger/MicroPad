# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Security regression tests: admin authentication, fail-closed defaults, and CSRF.

Covers P0.1 (unauthenticated API / SSRF / HA-token leak).  The existing test
`client` fixture runs loopback and without a secret, which is the allowed local
dev posture; these tests explicitly simulate a remote attacker (non-loopback
client) and/or a configured admin secret.
"""

from __future__ import annotations

import pytest

from micropad.app import create_app
from micropad.security import admin_secret, bind_loopback, secure_bind

REMOTE = {"REMOTE_ADDR": "192.0.2.10"}
# The admin secret used throughout these tests (kept in a constant so it is a
# variable reference, not a hardcoded-password literal at each call site).
ADMIN = "topsecret"


class _RemoteClient:
    """Proxy that forces a non-loopback remote address on every request."""

    def __init__(self, client) -> None:
        self._client = client

    def __getattr__(self, name: str):
        fn = getattr(self._client, name)

        def wrapper(*args, **kwargs):
            overrides = dict(kwargs.pop("environ_overrides", {}))
            overrides.update(REMOTE)
            return fn(*args, environ_overrides=overrides, **kwargs)

        return wrapper


def remote_client(app):
    return _RemoteClient(app.test_client())


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def csrf() -> dict[str, str]:
    return {"X-Requested-With": "XMLHttpRequest"}


# --- public routes stay anonymous -------------------------------------------


@pytest.mark.parametrize(
    ("path", "method"),
    [("/healthz", "get"), ("/api/meta", "get"), ("/", "get")],
)
def test_public_routes_stay_anonymous(store, path: str, method: str) -> None:
    app = create_app(store.path, admin_secret=ADMIN)
    response = getattr(remote_client(app), method)(path)
    assert response.status_code == 200


# --- sensitive routes require a valid secret on non-loopback ------------------


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/config", "get"),
        ("/api/config", "post"),
        ("/api/generate", "get"),
        ("/api/load-current-page", "get"),
        ("/api/validate", "post"),
        ("/api/ha/test", "post"),
        ("/api/ha/entities", "get"),
        ("/api/upload/api", "post"),
        ("/api/upload/ssh", "post"),
        ("/api/download/api", "get"),
        ("/api/download/ssh", "get"),
    ],
)
def test_remote_with_no_secret_configured_is_denied(store, path: str, method: str) -> None:
    app = create_app(store.path)  # no admin secret -> non-loopback must be denied
    response = getattr(remote_client(app), method)(path, json={} if method == "post" else None)
    assert response.status_code in (401, 403)
    assert response.json["ok"] is False


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/config", "get"),
        ("/api/config", "post"),
        ("/api/generate", "get"),
        ("/api/validate", "post"),
        ("/api/ha/test", "post"),
        ("/api/ha/entities", "get"),
        ("/api/upload/api", "post"),
        ("/api/upload/ssh", "post"),
    ],
)
def test_remote_with_wrong_secret_is_denied(store, path: str, method: str) -> None:
    app = create_app(store.path, admin_secret=ADMIN)
    client = remote_client(app)
    headers = bearer("wrong-token")
    if method in ("post", "put", "patch", "delete"):
        headers.update(csrf())
    response = getattr(client, method)(path, json={}, headers=headers)
    assert response.status_code in (401, 403)


def test_remote_with_valid_secret_is_allowed_for_read(store) -> None:
    app = create_app(store.path, admin_secret=ADMIN)
    response = remote_client(app).get("/api/config", headers=bearer("topsecret"))
    assert response.status_code == 200
    assert response.json["settings"]["ha_token"] == ""


def test_loopback_without_secret_remains_allowed_for_local_dev(store) -> None:
    app = create_app(store.path)  # no secret
    assert app.test_client().get("/api/config").status_code == 200


def test_remote_write_requires_csrf_header_even_with_valid_secret(store) -> None:
    app = create_app(store.path, admin_secret=ADMIN)
    response = remote_client(app).post(
        "/api/config", json={"settings": {"ha_url": "https://ha.example"}, "pages": []},
        headers=bearer("topsecret"),
        # no X-Requested-With
    )
    assert response.status_code == 403
    assert response.json["error"]["code"] == "csrf_required"


def test_remote_write_with_valid_secret_and_csrf_header_succeeds(store) -> None:
    app = create_app(store.path, admin_secret=ADMIN)
    client = remote_client(app)
    headers = {**bearer("topsecret"), **csrf()}
    payload = client.get("/api/config", headers=bearer("topsecret")).json
    response = client.post("/api/config", json=payload, headers=headers)
    assert response.status_code == 200


# --- no stored bearer token is forwarded to a changed URL anonymously ---------


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/ha/test", "post"),
        ("/api/ha/entities", "get"),
        ("/api/upload/api", "post"),
        ("/api/upload/ssh", "post"),
    ],
)
def test_anonymous_cannot_trigger_any_ha_or_upload_action(
    store, path: str, method: str
) -> None:
    """An anonymous attacker cannot trigger actions even when HA settings exist."""
    config = store.load().model_copy(deep=True)
    config.settings.ha_token = "real-token"  # noqa: S105
    config.settings.ha_url = "http://attacker.example:9000"
    store.save(config)

    calls = {"n": 0}

    class RecordingHA:
        def __init__(self, settings):
            self.settings = settings

        def test_auth(self):
            calls["n"] += 1
            return {"message": "unexpected"}

        def list_entities(self):
            calls["n"] += 1
            return []

        def list_states(self):
            calls["n"] += 1
            return []

        def deploy(self, config):
            calls["n"] += 1
            return

    app = create_app(
        store.path, admin_secret=ADMIN, ha_client_factory=lambda s: RecordingHA(s)
    )
    client = remote_client(app)
    response = getattr(client, method)(
        path, json={} if method == "post" else None, headers=bearer("bad-token")
    )
    assert response.status_code in (401, 403)
    # No HA/SSH action ran, so the stored token was never forwarded anywhere.
    assert calls["n"] == 0


def test_anonymous_cannot_rewrite_ha_url(store) -> None:
    before = store.load().settings.ha_url
    app = create_app(store.path, admin_secret=ADMIN)
    client = remote_client(app)
    payload = {"settings": {"ha_url": "http://attacker.example:9000"}}
    response = client.post("/api/config", json=payload)
    assert response.status_code in (401, 403)
    assert store.load().settings.ha_url == before


def test_anonymous_config_read_does_not_leak_token(store) -> None:
    config = store.load().model_copy(deep=True)
    config.settings.ha_token = "sentinel-token"  # noqa: S105
    store.save(config)
    app = create_app(store.path, admin_secret=ADMIN)
    response = remote_client(app).get("/api/config")
    assert response.status_code in (401, 403)
    assert b"sentinel-token" not in response.get_data()


# --- admin secret comes from the environment, never the repo -----------------


def test_admin_secret_reads_from_environment_only(monkeypatch) -> None:
    monkeypatch.setenv("MICROPAD_ADMIN_SECRET", "env-secret")
    assert admin_secret() == "env-secret"
    monkeypatch.delenv("MICROPAD_ADMIN_SECRET", raising=False)
    assert admin_secret() is None
    monkeypatch.setenv("MICROPAD_ADMIN_SECRET", "")
    assert admin_secret() is None


# --- fail-closed production bind ---------------------------------------------


@pytest.mark.parametrize(
    "bind",
    ["0.0.0.0:8080", ":8080", "198.51.100.5:8080"],
)
def test_non_loopback_bind_is_rejected_without_secret(monkeypatch, bind: str) -> None:
    monkeypatch.delenv("MICROPAD_ADMIN_SECRET", raising=False)
    assert bind_loopback(bind) is False
    with pytest.raises(SystemExit):
        secure_bind(default_bind=bind)


@pytest.mark.parametrize("bind", ["127.0.0.1:8080", "[::1]:8080", "localhost:8080"])
def test_loopback_bind_is_allowed_without_secret(monkeypatch, bind: str) -> None:
    monkeypatch.delenv("MICROPAD_ADMIN_SECRET", raising=False)
    assert bind_loopback(bind) is True
    assert secure_bind(default_bind=bind) == bind


def test_non_loopback_bind_is_allowed_with_secret(monkeypatch, bind="0.0.0.0:8080") -> None:
    monkeypatch.setenv("MICROPAD_ADMIN_SECRET", "topsecret")
    assert secure_bind(default_bind=bind) == bind