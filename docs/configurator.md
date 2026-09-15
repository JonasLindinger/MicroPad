# MicroPad Configurator

> AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.

The configurator is the Flask web app (`src/micropad/app.py`) that validates your
configuration, edits pages and key maps, discovers Home Assistant entities, and
generates or uploads the automation. It talks to the repo over these endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /` | web UI (`src/micropad/templates/index.html`, `static/js/*`) |
| `GET /healthz` | liveness probe → `{"ok": true, "service": "micropad-configurator"}` |
| `GET/POST /api/config` | read / validate-and-save configuration |
| `POST /api/validate` | validate without saving |
| `GET /api/meta` | contract version, key IDs, actions, item types **and their descriptors** (`item_type_meta`), payload ceilings (`limits`), device caps (`caps`), templates, default keymap |
| `GET /api/generate` | generated automation YAML + retained MQTT payloads |
| `POST /api/lint` | payload budgets (per page + catalog) and "what the device changes" findings for a candidate config |
| `POST /api/simulate` | the panel's prim model for one page (or the setup portal), rendered by the firmware core |
| `POST /api/ha/test` | test Home Assistant auth |
| `GET /api/ha/entities` | discover and cache Home Assistant entities |
| `POST /api/upload/api` | deploy via Home Assistant REST API |
| `POST /api/upload/ssh` | deploy via SFTP/SSH (see `docs/home-assistant.md`) |

## Panel preview and budgets

The editor shows the panel as the device will draw it. The picture is not redrawn
in JavaScript: `POST /api/simulate` runs `tools/micropad_sim.cpp`, which compiles
the shipped core (`firmware/micropad_core.cpp`) and answers with the core's prim
model for the page (296x128, FreeMono metrics, title/value clipping, selection
cursor, scrollbar, status icons) or for the setup portal view. Build the tool once
with `./scripts/build-simulator.sh`; when it is missing the endpoint answers 503
`simulator_unavailable` and the UI says so instead of showing an invented panel.

`POST /api/lint` reports the byte budgets and the changes the device would make:

- per-page payload bytes against 8192, the catalog against 16000 and against the
  pad's 16384-byte MQTT buffer (a catalog over the buffer is dropped by the MQTT
  client without an error), pages against 24 and items against 20;
- `field_clipped` warnings for display fields (title/name/state/unit) that exceed
  the firmware's 32-character budgets, and `identifier_too_long` errors for
  `page_id`/`entity`/`target_page`, which make the pad discard the whole payload.

The limits and caps come from `contracts/mqtt-contract.json` — the same values the
pad advertises on its retained `micropad/device` payload — and the byte sizes come
from the real generator, so the meters cannot drift from what a deployment
publishes. `docs/firmware.md` describes the firmware side.

Configuration is stored in `config.json` (git-ignored local) or the path in
`MICROPAD_CONFIG_PATH`. Secrets (`ha_token`, `ssh_key`) are redacted from every
public response — only `ha_token_configured` / `ssh_key_configured` booleans are
exposed. All placeholder values used in this documentation are neutral
(`192.168.0.100`, user `micropad`, password `replace-me`).

## Authentication

Sensitive API routes are fail-closed. The admin secret comes only from the
`MICROPAD_ADMIN_SECRET` environment variable (in production, the optional
`EnvironmentFile` `/etc/micropad/admin.env` described in `docs/deployment.md`):

- `GET /healthz` and `GET /api/meta` are anonymous and expose no sensitive data.
- With a secret configured, every other `/api/*` route requires
  `Authorization: Bearer <secret>` (constant-time comparison). Without a
  configured secret, sensitive routes are served to loopback clients only, and a
  non-loopback bind refuses to start — an unauthenticated public endpoint cannot
  come up silently.
- State-changing requests from non-loopback clients also require the
  `X-Requested-With: XMLHttpRequest` cross-site guard.
- The browser holds the secret in `sessionStorage` only (never in the URL, the
  persisted config, logs, or generated files) and sends it as a request header.
  On a `401` it shows the sign-in dialog; entering the secret re-loads the app.

Use HTTPS (reverse proxy) for remote `ha_url` values; `http` is accepted only as
a local-trust-network value such as `http://homeassistant.local:8123`.

## Pages editor

A configuration contains up to 24 pages. Each page has an id (a normalized
lowercase slug), a title, an optional parent (for navigation), up to 20 items,
and a per-page key-map override. Item types are: `category`, `light`, `switch`,
`script`, `button`, `scene`, `sensor`, `media_player`, `number`, `settings`, and
`back`. A `category` item requires a `target_page`; entity-backed item types
require an entity in the matching domain (`light.foo` for a `light`, etc.).
Editable starter templates (Spotify, Discord, Lights, generic media) are offered
by the pages editor.

## Key editor

Every input maps to an action. The fourteen key IDs are fixed by the contract:
`r0c0, r0c1, r0c2, r0c3, r1c0, r1c1, r1c2, r1c3, r2c0, r2c1, r2c2, r2c3, enc_up,
enc_down`. The global key map must contain all fourteen in canonical order; a page
may override any subset. Actions (from `src/micropad/ui_meta.py`):

- **Navigation:** `enter`, `back`, `home`, `navigate`, `keymap`.
- **Scrolling:** `scroll`, `scroll_up`, `scroll_down`.
- **Device:** `toggle`, `on`, `off`, `press`, `edit`, `confirm`.
- **Media:** `volume_up`, `volume_down`, `media_next`, `media_prev`.
- **System:** `settings`, `get_all_pages`, `none`.

`navigate` and `keymap` take a `target_page`; the device actions (except `none`,
`settings`, `get_all_pages`) take an `entity` argument.

## Entity discovery

`GET /api/ha/entities` calls Home Assistant's entity API with the stored token,
returns the discovered entities, and caches them in `entity_cache` so the UI can
autocomplete entity ids. Discovery requires a configured `ha_url` and `ha_token`;
**TLS verification defaults to on** (`verify_tls: true`). Entities are matched for
editing against the item-type domains, and the generator reflects current state
(light switch state, number value/min/max/step, media volume, sensor unit) into
generated retained payloads whenever the cached state is concrete. Unknown or
`unavailable` states are left alone rather than guessing.

## Validation

Validation is strict and real:

- **Model validation** (`src/micropad/models.py`): unknown fields are rejected,
  page-graph cycles/unknown parents are rejected, key maps must use only the
  fourteen known ids, global keys must be exactly the canonical set, item shape
  rules (category needs `target_page`; entity types need a matching-domain
  entity; `min <= max`).
- **Generation validation**: every publishable payload is generated and rejected
  if it exceeds its byte budget (page 8192, catalog 16000, keymap 16380).
- **Template safety**: user-controlled Jinja delimiters (`{{`, `{%`, `{#`) and
  malformed entity ids are rejected before any payload is embedded.
- **Net effect**: `POST /api/validate`, `POST /api/config`, and every upload
  path fail closed (HTTP 422 validation error / 502 upload error) rather than
  shipping a partial or unsafe configuration.

MicroPad is not safety-critical; always keep the Home Assistant UI as the
authoritative control surface.