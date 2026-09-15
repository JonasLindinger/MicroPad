# MicroPad Deployment

> AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.

This guide covers installing the configurator as a production systemd service on
a Linux host, updating it, reading logs, and uninstalling it. The broker host
placeholder used throughout is `192.168.0.100`; real credentials are entered only
through the configurator or the on-device setup portal, never in source.

## Container

`Dockerfile` builds the configurator as an image. The API is fail-closed, so the container
binds loopback and refuses to come up publicly unauthenticated: to reach it from a browser
you must set both the admin secret and the bind address, and publish the port.

```bash
docker build -t micropad-configurator .
docker run --rm -p 8080:8080 \
  -e MICROPAD_ADMIN_SECRET=<secret> \
  -e MICROPAD_BIND=0.0.0.0:8080 \
  -v "$PWD/data:/app/data" micropad-configurator
```

The image carries `contracts/`, because the configurator derives its item types, actions,
key ids, topic names and payload ceilings from `contracts/mqtt-contract.json` at import
time - an image without it starts and then fails on the first request. `data/` holds the
configuration and its history snapshots; mount it or the settings live and die with the
container. Never bake a real secret into the image.

The image also carries the host simulator (`build/host/micropad_sim`), compiled in a build
stage from the shipped firmware core. The panel preview and the byte budgets come from
that binary, so an image without it answers `503 simulator_unavailable` on `/api/simulate`
and the preview panel shows a build path instead of the pad's screen.

**No login on a trusted LAN.** The fail-closed default refuses to bind a non-loopback
address without a secret. An operator who wants the configurator open on their own network
(no sign-in dialog, as the pre-auth builds behaved) opts in explicitly - and every start
logs a warning, because anyone who can reach the port can read the stored Home Assistant
token and publish with it:

```bash
docker run --rm -p 8080:8080 \
  -e MICROPAD_ALLOW_UNAUTHENTICATED_LAN=1 \
  -e MICROPAD_BIND=0.0.0.0:8080 \
  -v "$PWD/data:/app/data" micropad-configurator
```

Writes from non-loopback clients still require the `X-Requested-With` guard, so a
cross-site form cannot publish anything. Use the secret (see below) whenever the network
is not entirely yours.

## Install

`scripts/setup.sh` deploys the app to `/opt/micropad/app`, runs it as the
unprivileged system user `micropad`, and keeps data in `/var/lib/micropad`. The
install is **root-gated**: it refuses to run unless invoked as root.

```bash
sudo ./scripts/setup.sh            # install; do NOT start the service yet
sudo ./scripts/setup.sh --enable-service   # install AND enable+start micropad.service
```

What the script does:

1. creates the `micropad` system user (no login shell);
2. copies `pyproject.toml`, `requirements.txt`, `config.example.json`, `src`,
   `gunicorn.conf.py`, and `deploy/` into `/opt/micropad/app`;
3. creates `.venv`, installs runtime requirements, then installs the package;
4. seeds `/var/lib/micropad/config.json` **only if absent** (re-runs never
   overwrite your edits) — the file is mode `0600`, owned by `micropad`, so
   credentials are readable only by the service user;
5. installs `deploy/micropad.service` into systemd and, with `--enable-service`,
   runs `systemctl enable --now micropad.service`.

The unit (see `deploy/micropad.service`) runs gunicorn from the deployment venv
(`/opt/micropad/app/.venv/bin/python`) with working directory
`/opt/micropad/app` (`gunicorn.conf.py`: 2 sync workers, 30 s timeout) with hard
hardening: `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`,
`ProtectHome`, `PrivateDevices`, empty `CapabilityBoundingSet`, and write access
restricted to `/var/lib/micropad`. The config path is
`/var/lib/micropad/config.json` (`MICROPAD_CONFIG_PATH`).

**Admin authentication (fail-closed).** Every sensitive `/api/*` route requires
the admin secret. The secret is read from the `MICROPAD_ADMIN_SECRET`
environment variable, which the unit loads from the optional `EnvironmentFile`
`/etc/micropad/admin.env` (create it mode `0600`, owned by root). Until that
file exists the service binds **loopback only** (`127.0.0.1:8080`) and is not
reachable from the network — that is the safe default. To expose the API on the
LAN (typically behind HTTPS or a reverse proxy), write:

```bash
sudo install -d -m 0700 /etc/micropad
# paste your secret; never commit it, never put it in config.json
echo 'MICROPAD_ADMIN_SECRET=replace-me' |
  sudo tee /etc/micropad/admin.env >/dev/null
sudo chmod 0600 /etc/micropad/admin.env
echo 'MICROPAD_BIND=0.0.0.0:8080' | sudo tee -a /etc/micropad/admin.env
sudo systemctl restart micropad.service
```

Prefer HTTPS (a reverse proxy such as Caddy/nginx) for any remote access. An
HTTP `ha_url` is accepted only as a local-trust-network value (e.g.
`http://homeassistant.local:8123`); prefer
`https://homeassistant.local:8123` when TLS is available.

Verify it is up:

```bash
curl http://127.0.0.1:8080/healthz
# -> {"ok": true, "service": "micropad-configurator"}
```

## Update

1. Review and pull the new source into the target workspace, then either re-run
   `sudo ./scripts/setup.sh` (idempotent; preserves your config) or, for a source
   refresh, re-run `python3 -m pip install --requirement requirements.txt
   --no-deps .` in `/opt/micropad/app/.venv`.
2. Restart the service:

```bash
sudo systemctl restart micropad.service
```

Re-running `setup.sh` never overwrites an existing `/var/lib/micropad/config.json`
because of the seed-only-if-absent guard. After changing configuration through the
UI or regenerating an automation, redeploy the automation to Home Assistant so the
retained MQTT topics and the automation stay in sync (see `docs/home-assistant.md`).

## Logs

The unit sets `PYTHONUNBUFFERED=1`; gunicorn writes access and error logs to
stdout/stderr, which systemd captures. Follow them with:

```bash
journalctl -u micropad.service -f          # follow live
journalctl -u micropad.service --since today
```

Use `systemctl status micropad.service` for unit state and the last logged lines,
and `systemctl is-enabled micropad.service` / `systemctl is-active
micropad.service` for enable/active status. The API error handlers return typed
JSON (`validation_error`, `generation_error`, `ha_error`, `ha_verification_error`,
`ssh_error`, `not_found`, `method_not_allowed`) so operators can filter on the
JSON `error.code` rather than scraping text.

## Uninstall

```bash
sudo systemctl disable --now micropad.service   # stop + disable start at boot
sudo rm -f /etc/systemd/system/micropad.service
sudo systemctl daemon-reload
sudo systemctl reset-failed micropad.service || true
# Optionally remove the filesystem footprint:
sudo userdel micropad || true
sudo rm -rf /opt/micropad                        # application tree
sudo rm -rf /var/lib/micropad                    # config + data (contains your config!)
```

Uninstalling `/var/lib/micropad` removes your configuration, including any
credentials stored there — back it up first if you may reinstall.

MicroPad is not safety-critical; always keep the Home Assistant UI as the
authoritative control surface when the service or network is unavailable.