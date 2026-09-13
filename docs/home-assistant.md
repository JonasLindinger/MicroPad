# MicroPad × Home Assistant

> AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.

This guide explains how MicroPad talks to Home Assistant and the two ways to
deploy the generated automation. The broker host placeholder used throughout is
`192.168.0.100` (user `micropad`, password `replace-me`); real values are entered
only through the configurator or the on-device setup portal, never in source.

## MQTT contract

MicroPad and Home Assistant share one versioned contract
(`contracts/mqtt-contract.json`, bindings in `firmware/protocol_contract.h` and
`src/micropad/constants.py`, contract version **1**). Five exact topics:

| Topic | Direction | Retain | Purpose |
|---|---|---|---|
| `micropad/event` | device → HA | no | decoded key/encoder action |
| `micropad/pages/all` | HA → device | yes | boot-time page catalog |
| `micropad/page/current` | HA → device | yes | current page payload |
| `micropad/keymap` | HA → device | yes | effective per-page key map |
| `micropad/power` | device → HA | yes | USB/power state (`usb_host` true/false) |

The generator (`src/micropad/generator.py`) turns a validated configuration into
one automation with id `micropad_controller` plus the three retained initial
publications (`micropad/pages/all`, `micropad/page/current`, `micropad/keymap`).
Payload ceilings are 1800 bytes per page, 16000 per catalog, 16380 per keymap,
with a 16384-byte firmware MQTT buffer. Generation is deterministic: the same
config produces byte-identical YAML, JSON, and MQTT payloads.

## API upload

`POST /api/upload/api` (implemented by `src/micropad/ha_deploy.py`) deploys
directly to a running Home Assistant over its REST API:

1. verifies auth (`/api/ha/test`), then create-or-update the automation by id;
2. calls `automation.reload`, then **reads back** the automation and confirms the
   structure matches what was generated;
3. re-generates with fresh entity state and publishes the three retained MQTT
   topics, verifying each publication.

Requirements: a Home Assistant long-lived access token
(`settings.ha_token`), the instance URL (`settings.ha_url`, e.g.
`http://homeassistant.local:8123`), and **TLS verification on by default**
(`settings.verify_tls` defaults to `true`). The token is redacted from every
public response (`ha_token_configured` reports only a boolean). A verified
evidence record (automation id, SHA-256, request order, published-topic list) is
emitted through the deployer's evidence sink.

## SSH upload

`POST /api/upload/ssh` (implemented by `src/micropad/ssh_upload.py`) targets
installations where the REST/automation path is not usable, by pushing the
generated YAML over SFTP/SSH with **strict host-key checking**:

- writes a temp copy next to `settings.remote_path` (default
  `/config/automations/micropad.yaml`);
- runs `settings.ssh_validate_command` (must contain `{temp_path}` once; default
  `test -s {temp_path}`) — rejects placeholders and non-absolute remote paths;
- atomically `mv`s it into place and runs `settings.ssh_reload_command` (default
  `ha core restart`).

`GET /api/download/ssh` returns a secret-free `upload_micropad.sh` operator
script that reproduces the same SFTP upload from environment variables
(`SSH_KEY`, `AUTOMATION_YAML`) without embedding credentials.

**Difference from API upload:** SSH upload transfers only the automation YAML and
reloads; it does **not** publish the three retained MQTT topics itself, and it
relies on your existing Home Assistant automation layer (and the reload to pick
it up) rather than creating the automation through the REST API. Choose SSH when
the device cannot be reached over the HA REST API, and API upload when it can
(the API path is fully verified end-to-end).

## Rollback

- **API upload:** because the automation is create-or-update by a fixed id, deploy
  replaces it. To roll back, re-generate from a known-good configuration and
  deploy again, or restore the previous automation in Home Assistant's automation
  editor. The retained MQTT topics are re-published on every successful deploy, so
  the device re-syncs to the new state on reconnect.
- **SSH upload:** the deployed file replaces `settings.remote_path`
  atomically. Keep a backup of the previous file; to roll back, `scp`/`sftp` your
  backup back to `remote_path` and run `settings.ssh_reload_command` again
  (`ha core restart`).
- **Local previews:** the live-deployment gate is hash-locked. Run
  `scripts/render_live_ha_preview.py --config <config> --output build/live-ha-preview`
  to produce `automation.json`, `automation.yaml`, `mqtt-publications.json`, and
  a `release.sha256`, approve that digest, then `scripts/apply_live_ha_preview.py`.
  **Operator note:** after a live apply, the automation read-back and the actual
  retained publications can drift from the rendered preview; re-render (regenerate
  `mqtt-publications.json`) using the same config to keep the digest, preview, and
  live broker in lock-step, then re-verify retained payloads with
  `scripts/verify_live_mqtt.py`. That live-broker verifier requires the
  `paho-mqtt` package (it is listed in `requirements-dev.txt`) and `jsonschema`;
  it reads the broker endpoint from the git-ignored `config.json`.

MicroPad is not safety-critical; always keep the Home Assistant web UI as the
authoritative control surface and verify deployment behaviour on your own
installation.