# MicroPad Security and Release

> AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.

This guide documents how secrets are handled, what approval gates exist before a
public release, and how incidents are triaged. All examples use the neutral
placeholders (`192.168.0.100`, user `micropad`, password `replace-me`); real values
are never committed, never rendered in docs, and never written into generated
artifacts beyond the operator's own environment.

## Secrets

- **Source is secret-free.** Grep-able properties are enforced: no routable real
  IP, no `mqtt://`/`wss://` credential URLs, no `changeme`/`replace-me`-style
  unfilled placeholders in any deployed automation. The firmware and Python
  sources carry only `192.168.0.100` / `micropad` / `replace-me`.
- **Runtime credentials** (Wi-Fi/MQTT) are entered only through the on-device
  setup portal and live in NVS; Home Assistant token and SSH key live in
  `config.json` (`/var/lib/micropad/config.json`, mode `0600`, owned by
  `micropad`) and are **redacted from every public API response** — the API sends
  `ha_token_configured`/`ssh_key_configured` booleans and empty strings, never the
  secrets. `public_config()` enforces this at the boundary.
- **Live-broker tooling** reads the token/password from the git-ignored
  `config.json` in memory only and never writes credentials anywhere; its
  evidence outputs (topic, retain flag, schema result, payload SHA-256,
  expected payload SHA-256) are secret-free.
- **The AI notice** is enforced as a release gate: `scripts/audit_notices.py`
  audits every tracked doc/source file for the exact notice and fails on any
  missed file, so no release artifact can silently lose its AI-assistance
  disclosure.
- `.gitignore` keeps `config.json`, `.venv/`, build output, and cache dirs out of
  version control; local state is never tracked.

## Approval gates

A public release is gated, not fire-and-forget:

1. **Tests green.** Backend and firmware compile matrices pass
   (`tests/`, `scripts/compile-firmware.sh`, host/static suites).
2. **AI-notice audit clean.** `scripts/audit_notices.py` returns an empty missing
   list (exit 0) over tracked docs and sources.
3. **Docs verified.** `tests/release/test_docs_release.py` asserts every release
   doc exists, is English, carries the exact notice, and contains its mandated
   sections; `tests/test_foundation.py` audits the rooted source/doc set.
4. **Preview digest approved.** For a live Home Assistant deployment, the operator
   renders `automation.json`, `automation.yaml`, `mqtt-publications.json`, and
   `release.sha256`, approves an explicit `--approved-sha256`, and
   `scripts/apply_live_ha_preview.py` refuses to open a network connection before
   the digest matches. Then the retained topics are verified against the live
   broker with `scripts/verify_live_mqtt.py`.
5. **Hardware gate honesty.** No hardware = no pass: the acceptance rows stay
   unchecked (see `docs/hardware-acceptance.md`) until an operator observes them.
   The final verification on the build machine compiles/static-checks only.

## Public release

Publishing this project means releasing the repo as it exists here. Because it is
built as a clean-room, personal project, a public release must:

- exclude `config.json`, `.venv/`, build and cache outputs (already git-ignored and
  never tracked);
- carry the exact AI-assistance notice in every authored doc/source file
  (guaranteed by the audit, since it scans `git ls-files` plus explicitly passed
  paths);
- keep all examples on the neutral placeholders so readers cannot be pointed at a
  real host or credential;
- expose no upstream text: all documentation in this repo is original neutral
  English, written from the shipped interfaces, with no copyright attribution to
  third parties;
- state plainly that the project is **not safety-critical** and that behaviour
  must be verified on the reader's own hardware.

## Incident response

- **What counts as an incident (examples).** A leaked credential committed to the
  repo, a version-conflict between the contract JSON and the firmware bindings, a
  retained-topic payload carrying `{{ ... }}` templates to the device, or a rollout
  that silently dropped the AI notice.
- **Contain.** Stop the affected service (`sudo systemctl stop micropad.service`)
  or revoke the leaked token/key in Home Assistant / the broker immediately; never
  reply with credentials in any channel.
- **Diagnose.** Reproduce in a clean venv/sketch, capture `journalctl -u
  micropad.service`, and pin the failing contract binding to the exact schema and
  the exact topic before editing.
- **Confirm ownership path.** The device is not safety-critical; the authoritative
  control surface is Home Assistant, so a compromised or failed MicroPad must never
  be treated as an emergency stop on the system it controls.
- **Correct & harden.** Fix at the source, bump the contract version or the
  affected component together (firmware, generator, meta endpoint, browser, and
  the JSON schemas bind to the same constants), re-run the tests and the AI-notice
  audit, and re-verify any live retained payloads against the re-rendered preview.

MicroPad is provided as-is, without warranty, for non-safety-critical use only.