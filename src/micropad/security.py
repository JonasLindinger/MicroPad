# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Request authentication and CSRF guard for the MicroPad configurator API.

Security model (fail-closed):

* The admin secret lives ONLY in the ``MICROPAD_ADMIN_SECRET`` environment
  variable.  It is never read from the repository, the persisted config, or any
  generated file, and it is never logged.
* When a secret IS configured, every sensitive ``/api/*`` route requires a
  matching ``Authorization: Bearer <secret>`` header.  The comparison is
  constant time (``hmac.compare_digest``), so a wrong secret cannot be
  recovered by timing.
* When NO secret is configured, sensitive routes are served to loopback
  clients only.  A process that would expose the API on a non-loopback
  address without a secret refuses to start (``secure_bind``), so an
  unauthenticated production endpoint cannot come up silently.
* ``/healthz`` stays anonymous and returns no sensitive data.

The secret therefore never needs to leave ``sessionStorage`` in the browser:
the frontend sends it as a request header, never in the URL, the config, logs,
or generated files.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import sys

#: Environment variable holding the admin secret (the value is the variable NAME,
#: not a secret; the secret itself only ever lives in the environment).
SECRET_ENV = "MICROPAD_ADMIN_SECRET"  # noqa: S105
#: Environment variable overriding the bind endpoint (default is loopback).
BIND_ENV = "MICROPAD_BIND"
#: Environment variable that explicitly permits serving the API without a secret on a
#: non-loopback bind. Off by default: this endpoint can read the stored HA token and
#: publish to Home Assistant, so an open deployment has to be a deliberate, visible
#: choice ("my own LAN, no login") rather than an accident of a missing variable.
ALLOW_UNAUTHENTICATED_LAN_ENV = "MICROPAD_ALLOW_UNAUTHENTICATED_LAN"
#: Default bind: loopback only, so a misconfigured deployment is not public.
DEFAULT_BIND = "127.0.0.1:8080"

#: Custom header required on state-changing requests (a non-simple header that
#: a cross-site HTML form cannot set without a CORS preflight, which we never grant).
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "XMLHttpRequest"

#: Methods that change state and therefore need the CSRF guard.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Routes served without authentication.  ``/healthz`` carries no sensitive data.
PUBLIC_PATHS = frozenset({"/", "/healthz", "/api/meta"})


def admin_secret() -> str | None:
    """Return the configured admin secret, or ``None`` when unset/empty."""
    value = os.environ.get(SECRET_ENV)
    return value if value else None


def allow_unauthenticated_lan() -> bool:
    """True when the operator opted into an open, secret-less API on a non-loopback bind."""
    value = (os.environ.get(ALLOW_UNAUTHENTICATED_LAN_ENV) or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def is_loopback(remote_addr: str | None) -> bool:
    """True only for a client address that is genuinely on the loopback interface."""
    if not remote_addr:
        return False
    try:
        return ipaddress.ip_address(remote_addr).is_loopback
    except ValueError:
        # A non-IP literal is only trusted when it is the well-known loopback name.
        return remote_addr == "localhost"


def constant_time_equal(candidate: str | None, secret: str) -> bool:
    """Compare a supplied token against the admin secret in constant time."""
    if not candidate:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), secret.encode("utf-8"))


def bearer_token(value: str | None) -> str | None:
    """Return the token from an ``Authorization: Bearer <token>`` header value."""
    if not value or not value.startswith("Bearer "):
        return None
    token = value[len("Bearer ") :].strip()
    return token or None


def bind_loopback(bind: str) -> bool:
    """True when a gunicorn bind endpoint is loopback-only.

    An empty host (``":8080"``) means gunicorn binds every interface, which is
    NOT loopback.
    """
    host = bind.rsplit(":", 1)[0].strip().strip("[]")
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def secure_bind(default_bind: str = DEFAULT_BIND) -> str:
    """Resolve the bind endpoint, refusing to expose the API unauthenticated.

    Raises ``SystemExit`` when no admin secret is configured and the resolved
    bind would be reachable from outside the host.  This makes a misconfigured
    production start fail loudly instead of coming up insecure.  An operator who
    wants that posture anyway (a trusted LAN, no login) sets
    ``MICROPAD_ALLOW_UNAUTHENTICATED_LAN=1``; the start then logs a warning on
    every boot instead of failing.
    """
    bind = os.environ.get(BIND_ENV) or default_bind
    if not bind_loopback(bind) and admin_secret() is None:
        if allow_unauthenticated_lan():
            print(
                f"WARNING: binding {bind} WITHOUT {SECRET_ENV} because "
                f"{ALLOW_UNAUTHENTICATED_LAN_ENV} is set. Anyone who can reach this "
                "port can read the stored Home Assistant token and publish to Home "
                "Assistant. Use it only on a network you trust.",
                file=sys.stderr,
            )
            return bind
        raise SystemExit(
            f"refusing to bind {bind!r} without {SECRET_ENV}: an unauthenticated "
            "endpoint must not be exposed on a non-loopback address"
        )
    return bind
