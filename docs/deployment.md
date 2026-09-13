# MicroPad Deployment

> AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.

This guide covers installing the configurator as a production systemd service on
a Linux host, updating it, reading logs, and uninstalling it. The broker host
placeholder used throughout is `192.168.0.100`; real credentials are entered only
through the configurator or the on-device setup portal, never in source.

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

The unit (see `deploy/micropad.service`) runs gunicorn on `0.0.0.0:8080`
(`gunicorn.conf.py`: 2 sync workers, 30 s timeout) with hard hardening:
`NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome`,
`PrivateDevices`, empty `CapabilityBoundingSet`, and write access restricted to
`/var/lib/micropad`. The config path is `/var/lib/micropad/config.json`
(`MICROPAD_CONFIG_PATH`).

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