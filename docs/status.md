# MicroPad — status: what is done, what is left

> AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.

Branch `feat/cleanroom-implementation` · pushed to `origin` **and** `NEWEST-TEst`. Code state
`d2ec93b`: 11 commits, 63 files changed since `15b6ede`. Every claim below was verified with real
runs; nothing is "should work".

**Current baseline** (real `arduino-cli` build, pinned `hwcdc/PSRAM` FQBN):
flash **1 006 811 B (76 %)** · static RAM **173 364 B (52 %)** · 302 909 B flash free.
**Tests:** 824 pytest (44 of them browser) · 142 firmware static · host core binary · 193 release.

---

## ✅ Done (shipped, tested, pushed)

### Phase A — makes adding features cheap

| ID | What | Commit | Proof |
|---|---|---|---|
| **A1** | **Contract is the single source of truth.** `contracts/mqtt-contract.json` drives everything; `scripts/generate_contract.py --write/--check` generates `firmware/protocol_contract.h` + the browser key-ID block; Python derives at import | `ee98fcf` | 8 drift gates (codegen, enum order = wire order, firmware caps = contract caps, descriptor table = contract, backend rules, `/api/meta`, browser fixture) |
| **A2** | **One descriptor row per item type** (`ItemTypeDescriptor`: default action, alternate action, editable) drives `actionForSelectedItem`, `models.py`, `generator.py`, `/api/meta`, the editor | `ee98fcf` | host test `testItemTypeDescriptorTable` + drift gate |
| **A3** | **Pad advertises its own limits**: retained `micropad/device` (`fw`, `contract`, `caps` from its own constants) | `ee98fcf` | `/api/meta` exposes `limits`/`caps`/`item_type_meta` |
| **A4** | **Firmware-in-the-loop simulator**: `tools/micropad_sim.cpp` compiles the shipped core; the sketch's row loop moved into the core (`fillPageSnapshot`) so panel and preview share one builder | `6e2b1ce` | `tests/test_simulator.py`; fixture recorded from the real binary + a drift gate |
| **A5** | **`/api/simulate`** renders a page (or the portal) through that binary; 503 when missing, 400 with the offending field | `6e2b1ce` | route tests |
| **A6** | **Config-lint CLI** `scripts/lint_config.py` (exit 0/1/2, `--strict`, `--json`) + **a CI hole closed**: the `firmware-host` stage now also runs `test_core.cpp` and the 142 static tests (they ran nowhere in CI before) | `18644b9` | stage dry-run green |
| **A7** | **Golden-file tests**: automation YAML + the three published payloads frozen in `tests/fixtures/golden/`, regenerate with `MICROPAD_UPDATE_GOLDEN=1` | `d0f319d` | negative test: mutating a golden fails two tests |
| **A8** | **Pre-commit gate** `.githooks/pre-commit` (ruff, mypy, contract drift, firmware host ≈ 4 s) — documented, deliberately **not** auto-enabled | `d0f319d` | hook dry-run |
| **A9** | **Developer guide** `docs/developer.md`: recipes (action / item type / topic / template), gate table, conventions tests enforce | `d0f319d` | — |

### Phase B — the config website

| ID | What | Commit | Proof |
|---|---|---|---|
| **B1** | **Faithful panel preview**: `panel-preview.js` draws the core's prim model as SVG (296×128, monochrome, FreeMono) + portal toggle — no layout knowledge in the browser | `6e2b1ce` | browser tests against a real recorded model |
| **B2** | **Budget meters**: page/catalog/MQTT-buffer/pages/items from `/api/lint` | `6e2b1ce` | browser test |
| **B3** | **"What the device changes"**: `field_clipped` warnings vs `identifier_too_long` errors with `where` paths | `6e2b1ce` | route + browser tests |
| **B4** | **Hardware-shaped keymap board**: 3×4 + encoder; each face shows the effective action and its origin (`global` / `inherited` / `override`) | `a8d4262` | browser test incl. the scope transitions |
| **B5** | **Page generation from the entity cache**: `/api/entity-groups` + `/api/build-pages` (one page per domain, split at 20, 24-page ceiling, real `number` ranges, clipping reported in `notes`) + UI buttons | `a9be614` | 8 backend + 3 browser tests |
| **B7** | **Config history / diff / restore**: content-addressed snapshots beside the config (mode 600, git-ignored), list, structural diff that never prints secrets, confirmed restore in the UI | `26f41d0` | 10 backend + 3 browser tests incl. traversal + stale revision |

### Phase C — device features

| ID | What | Commit | Proof |
|---|---|---|---|
| **C1** | **Long press**: a hold dispatches the item's *alternate* action (light/switch → off); timing in the core (`LONG_PRESS_MS` 600, once per hold), wake press exempt, no protocol change, +184 B flash / +48 B RAM | `a6467cb` | host test (tap/hold/re-arm/None) + static pin |
| **C2** | **Diagnostics**: retained `micropad/diag` (uptime, MQTT connects, parse accept/reject counters, dropped events, heap high-water), formatter in the core, 256 B stack buffer, +768 B flash / +40 B RAM | `38cffc0` | host test byte-for-byte incl. worst case |
| **C4** | **Four new item types**: `cover`, `fan`, `input_boolean`, `lock` — each one contract row + one firmware descriptor row + the HA service mapping; the editor, `/api/meta` and the validation follow automatically. Tapping toggles (a lock *locks*), holding turns off (a lock *unlocks*); a cover has no hold action because HA's cover integration has no `turn_off` | `d2ec93b` | 7 new generator service rows, host descriptor assertions, all item-type pins updated. +64 B flash, 0 B RAM |

---

## 🔜 Left, and unblocked

| ID | Item | Effort |
|---|---|---|
| **C4b** | **`select`/`input_select` item type** — the encoder cycles the options; needs the option list in the payload plus a "next option" behaviour, i.e. a small contract decision | M |
| **C6** | **Icon/glyph set** in the prim model (wifi-error, lock, play/pause, thermometer, brightness) | S |
| **C7** | **Portal: factory reset + connection result** (reset part; OTA is bigger) | M |
| **C8** | **Per-page refresh policy** + dashboard template (periodic partial refresh) | M |
| **C9** | **Breadcrumb depth in the title strip** + long-press context menu on a row | S–M |
| **B8** | **Post-upload read-back**: after upload, re-read the retained topics and compare (`scripts/verify_live_mqtt.py` is the basis) | S–M |
| **B9** | **German/English, dark mode, mobile layout** of the configurator | S–M |
| **C5** | **Dimmer interaction** (hold + encoder steps a light's brightness) | M |

## ⛔ Left, blocked on an open decision

| ID | Item | The decision |
|---|---|---|
| **B6** | **Live device mirror + remote key press** — would have made the last debugging session much faster | The backend needs **broker credentials** (a new secret surface in the configurator, redaction, and a paho-mqtt dependency). Or: skip the mirror, do only remote key press via HA's `mqtt.publish` service (uses the existing HA token). |
| **C1b** | **Double press + encoder acceleration** (rest of C1) | A *contract* decision: a repeat/step parameter, or a second action + target per key (≈ +1.8 KB RAM) — or drop double press and do only encoder acceleration. |
| **B12** | **"One page per *room*"** | Needs the HA **area registry** (WebSocket API / a second credential), not `/api/states`. |
| **B11** | **Move payload decoding into the core** (removes the last mirrored logic and ArduinoJson from the hot path) | Device-facing refactor of the MQTT callback — needs a hardware test pass to be called done. |

## 🔌 Left, needs the real pad

| ID | Item |
|---|---|
| **D1** | Battery gauge (needs a PCB revision) |
| **D2** | Wall-dashboard mode (timer wake + deep sleep) |
| **D3** | Encoder push button (verify the part first) |
| **C1/C2** | the shipped gestures/diagnostics are host-verified only — one press on the pad and one look at `micropad/diag` would close them |
| **C3** | Notification banner (HA → pad): firmware render work, then a real-pad check |
| **C10** | Multi-device support |

---

## How to verify any of this yourself

```bash
bash scripts/test_firmware_contract.sh        # contract + core host + 142 static (~2 s)
.venv/bin/python -m pytest -q                 # everything (~75 s)
.venv/bin/python -m pytest tests/browser -q   # just the UI in Chromium (~30 s)
python3 scripts/lint_config.py config.json    # would the pad store/show this config
python3 scripts/generate_contract.py --check  # generated sources not stale
./scripts/build-simulator.sh                  # the host binary the preview uses
./scripts/compile-firmware.sh --one "esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,PSRAM=opi"
```

Explicitly **not** done, on purpose: a second protocol version, any cloud dependency, a scripting
engine on the device, bigger buffers, OTA outside the portal. ("Don't do" list kept honest: see the
roadmap at `docs/roadmap.md` for effort/risk per item and the reasoning.)
