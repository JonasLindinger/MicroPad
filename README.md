# MicroPad — Wi-Fi MQTT Controller for Home Assistant

> AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.

MicroPad is a small ESP32-S3 desk controller with a 2.9" e-paper display, a 3×4
key matrix, and a rotary encoder. It talks to Home Assistant over MQTT and shows
clickable pages of your devices, media, scripts, scenes, and numbers. This
repository contains the firmware, the Home Assistant automation generator, the
web configurator, and the deployment/service assets.

## Safety

- **Not safety-critical.** This project is a convenience input device. Do not use
  it, or rely on it, for safety-critical applications (life-safety, medical,
  security, or anything where a missed or delayed press could cause harm). The
  room always retains the Home Assistant UI as the authoritative control surface.
- **Verify on your own hardware.** Firmware behaviour is validated by the compile
  matrix and host/static suites on this build machine, but no physical MicroPad
  hardware was available during development. Every hardware acceptance row is
  intentionally unchecked until an operator executes it on real hardware. Never
  assume a feature works on your specific board; test it.
- **Neutral placeholders only.** Source contains no real credentials. The broker
  host placeholder is `192.168.0.100`, the service user is `micropad`, and the
  placeholder password is `replace-me`. Real Wi-Fi/MQTT credentials are entered
  only through the on-device setup portal and stored in NVS.
- **Charge-only chargers.** A charge-only wall charger (no enumerated USB host)
  intentionally follows normal battery sleep behaviour: no power icon is drawn
  and light sleep is allowed. Only an enumerated USB data host keeps the device
  fully awake. See `docs/hardware-acceptance.md`.

## Reproducibility

The Python dependency graph is locked: `uv.lock` is the resolved source of
truth, and `requirements-lock.txt` is its content-hash materialization (every
wheel pinned with `sha256`, regenerated with `uv export --frozen`). The build
backend is pinned exactly (`setuptools==84.0.0`). This freezes dependency
*versions and content*; it does **not** claim byte-identical builds, because
pip/setuptools release artifacts can still vary between environments. Generated
automation/payload artifacts are deterministic per configuration (see below).

## Quick start

Create a virtualenv and install the package and dev tools (Ubuntu/Debian shown):

```bash
python3 -m venv .venv
.venv/bin/pip install --requirement requirements-dev.txt
```

For a content-hash-pinned runtime install (every transitive dependency locked in
`uv.lock` and materialized as `requirements-lock.txt` with `sha256` hashes)
instead use:

```bash
.venv/bin/pip install --requirement requirements-lock.txt
```

Run the configurator locally for development (Flask dev server):

```bash
.venv/bin/python -m flask --app micropad.wsgi:app run --host 127.0.0.1 --port 8080
```

Check it is healthy:

```bash
curl http://127.0.0.1:8080/healthz
# -> {"ok": true, "service": "micropad-configurator"}
```

For production, install the systemd service under `/opt/micropad/app` (root):

```bash
sudo ./scripts/setup.sh --enable-service
journalctl -u micropad.service -f
```

The on-device network setup and the build/flash workflow are described in
`docs/firmware.md`.

## Firmware

The firmware lives in `firmware/` (`MicroPad_HA_Controller.ino`,
`micropad_core.*`, and `protocol_contract.h`). It is built with Arduino CLI 1.5.1,
ESP32 core 3.3.11, GxEPD2 1.6.9, ArduinoJson 7.4.3, and PubSubClient 2.8.

```bash
./scripts/install-arduino-toolchain.sh        # pin the exact toolchain
./scripts/compile-firmware.sh --print-matrix  # list the three pinned FQBNs
./scripts/compile-firmware.sh                 # compile all matrix FQBNs
```

See `docs/firmware.md` for the pin map, build/flash commands, the setup portal,
and sleep behaviour.

## Home Assistant

A single MQTT automation (id `micropad_controller`) turns `micropad/event` and
`micropad/power` publications into device actions and re-publishes the
authoritative `micropad/pages/all`, `micropad/page/current`, and `micropad/keymap`
retained payloads. The automation and the retained initial payloads are generated
by `src/micropad/generator.py` and are byte-identical for the same configuration.

You can deliver the generated automation two ways:

- **API upload** — the configurator writes the automation directly to Home
  Assistant via its REST API and verifies the read-back, then publishes the
  retained MQTT payloads.
- **SSH upload** — the configurator uploads a YAML file to your Home Assistant
  host over SFTP/SSH with strict host-key checking and reloads, for setups where
  the REST API path is not available.

Both are covered in `docs/home-assistant.md`.

## Configurator

The Flask web configurator (`src/micropad/app.py`) validates your configuration,
lets you edit pages, key maps, and entity bindings in the browser, discovers
Home Assistant entities, and generates or uploads the automation.

- `GET /api/config` — current public configuration (credentials redacted).
- `POST /api/config` — validate and save a configuration.
- `POST /api/validate` — validate without saving.
- `POST /api/upload/api` — deploy via the Home Assistant REST API.
- `POST /api/upload/ssh` — deploy via SFTP/SSH.
- `GET /api/meta` — contract version, the fourteen key IDs, actions, item types,
  templates, and the default keymap.

See `docs/configurator.md` for the pages editor, key editor, entity discovery,
and validation rules, and `docs/deployment.md` for install/update/logs/uninstall.

---

Copyright and licensing are intentionally unspecified; this is a personal
clean-room project. All content above is written in neutral English and uses only
the placeholder values described in the **Safety** section.