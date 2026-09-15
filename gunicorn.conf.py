# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Gunicorn configuration for the MicroPad configurator.

The bind endpoint is resolved through ``secure_bind``: when no admin secret is
set, only a loopback bind is permitted, and a non-loopback bind refuses to start
rather than exposing the API unauthenticated.  Set ``MICROPAD_ADMIN_SECRET`` (and
optionally ``MICROPAD_BIND``) in the deployment environment.
"""

import micropad.security as security

bind = security.secure_bind()
workers = 2
worker_class = "sync"
timeout = 30
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
capture_output = True