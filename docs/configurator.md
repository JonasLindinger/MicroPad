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
| `GET /api/entity-groups` | cached domains with entity counts and the item type each maps to |
| `POST /api/build-pages` | page drafts built from cached entities (nothing is saved) |
| `POST /api/upload/api` | deploy via Home Assistant REST API |
| `POST /api/upload/ssh` | deploy via SFTP/SSH (see `docs/home-assistant.md`) |

## Configuration history

Every save leaves a revision next to the configuration file
(`config.json.history/<UTC stamp>-<content hash>.json`), so a bad edit is a restore
instead of a re-typing session:

- `GET /api/history` lists the revisions (metadata only, newest first), `GET
  /api/history/<id>` returns one revision with the structural diff against the
  current configuration, and `POST /api/history/<id>/restore` makes it current again
  through the normal validated save path.
- Revisions are **content-addressed**: an unchanged document is not recorded, so the
  UI's autosave does not bury the interesting revisions, and restoring a state that
  is already in the history adds no duplicate. The newest 20 are kept.
- The diff is structural ("page \"office\" added (3 items)", "global keymap r0c0:
  home -> back") rather than a text diff, and it compares secrets without printing
  them: a changed Home Assistant token reads `ha_token: changed`.
- Snapshot ids are validated against an exact pattern before any file is opened, so
  a revision id from the browser can never escape the history directory.
- Snapshots contain the same credentials as the configuration itself: they are
  written with mode 600, live beside the config file and are git-ignored. They are
  never part of the release manifest.

## Keymap editor

The key editor is laid out like the hardware: three rows of four keys next to the
encoder, in the same arrangement the operator's fingers find on the pad. Each key
face shows its id and the action that key currently performs, and `data-binding-origin`
marks where that action comes from — `global`, `inherited` (from global or an
ancestor page) or `override` (this page's own) — so a page's whole keymap is readable
without clicking through fourteen keys. Selecting a face loads it into the binding
panel below, which is the only place a binding is edited, and the scene remains the
single source of the effective action (`keymap-model.js`).

## Page generation

"One page per domain" is one click. `GET /api/entity-groups` reports the domains in
the cached entity list with their counts and the item type each maps to (that mapping
comes from the contract's `item_type_meta`, so it cannot drift from the firmware).
`POST /api/build-pages` turns the cache into page drafts: one page per domain, split
when a domain holds more entities than a page takes (20), named "Lights" / "Lights 2",
with item types that match the entity domains, `number` items carrying the entity's
real min/max/step, and names/states/units clipped to the firmware's field caps — every
shortening is reported in `notes` rather than applied silently. Pages stop at the
pad's 24-page ceiling, and drafts are appended to the open configuration (which
autosaves like any other edit); publishing to the pad stays the normal deploy action.
Domains with no item type are listed in `skipped`, never dropped quietly.

Rooms are not offered yet: the REST endpoints the configurator uses (`/api/states`)
carry no area information. Area grouping would need the area registry (WebSocket API),
which is a separate decision.

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

The same analysis is available without the web UI, for CI or a pre-deployment
check:

```bash
python3 scripts/lint_config.py config.json          # 0 publishable, 1 errors, 2 unreadable
python3 scripts/lint_config.py config.json --strict # warnings fail too
python3 scripts/lint_config.py config.json --json   # machine-readable
```

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
`script`, `button`, `scene`, `sensor`, `media_player`, `number`, `cover`, `fan`,
`input_boolean`, `lock`, `settings`, and `back`. A `category` item requires a
`target_page`; entity-backed item types require an entity in the matching domain
(`light.foo` for a `light`, etc.). Tapping a `cover`/`fan`/`input_boolean` toggles it
and holding turns it off; a `cover` has no hold action (the cover integration has no
turn_off service); a `lock` locks on tap and unlocks on hold, because the lock
integration has no toggle service.
Editable starter templates (Spotify, Discord, Lights, generic media) are offered
by the pages editor.

### How a row is driven (the `control` field)

Each item carries a `control` that says how the pad treats the row:

- **`button`** (default) — the row is a target: select it, press Enter to run its
  action. Unchanged behaviour.
- **`slider`** — the row is a level: on top of the normal press behaviour, turning
  the encoder *steps its value* without an Enter press first (the level is written
  back to Home Assistant through the attribute that means "level" for that domain:
  `brightness_pct`, `percentage`, `position`, `volume_level` or
  `number.set_value`), and the panel draws a slider bar in the value column.
  Stepping is clamped to the item's `min`/`max` by its `step`; at either bound the
  encoder scrolls on instead of getting stuck.

The editor offers `slider` **only** for the item types that carry an adjustable
value — light, fan, cover, media_player, number (`adjustable` in the contract;
`/api/meta` carries it, so the select and the validation agree by construction
rather than by a hard-coded list in the browser). A `slider` on any other type is
rejected on save, exactly like a missing entity; if you change an item's type to a
type without a level, the control falls back to `button` in the same write instead
of leaving a config the pad cannot use.

### The state switch

Home Assistant states like `on`/`off` are shown as a checkbox next to the state
field, and checking it writes `on`/`off`. It appears only for values it can
represent (`on`, `off`, `true`, `false` or an empty state) so it can never turn a
reading such as `23.4` into a boolean. For anything else — a sensor value, a media
state, `unavailable` — the text field stays the only input. On the pad itself the
same two-valued states are drawn as a checkbox instead of the word, so what the
editor suggests is what the panel shows.

## Key editor

Every input maps to an action. The fourteen key IDs are fixed by the contract:
`r0c0, r0c1, r0c2, r0c3, r1c0, r1c1, r1c2, r1c3, r2c0, r2c1, r2c2, r2c3, enc_up,
enc_down`. The global key map must contain all fourteen in canonical order; a page
may override any subset. Actions (from `src/micropad/ui_meta.py`):

- **Navigation:** `enter`, `back`, `home`, `navigate`, `keymap`.
- **Scrolling / levels:** `scroll`, `scroll_up`, `scroll_down`, `adjust`.
- **Device:** `toggle`, `on`, `off`, `press`, `edit`, `confirm`.
- **Media:** `volume_up`, `volume_down`, `media_next`, `media_prev`.
- **System:** `settings`, `get_all_pages`, `none`.

`navigate` and `keymap` take a `target_page`; the device actions (except `none`,
`settings`, `get_all_pages`) take an `entity` argument.

### Gestures per key

Below the action picker each key has a **Hold** and a **Double press** block. Each
one holds its own action, entity and target page, so `hold` can mean something
entirely different from the tap ("tap turns the desk light on, hold turns the
ceiling off"). `Clear hold` / `Clear double press` unset the gesture again, which
is what the *omitted* gesture means: the key behaves exactly as it did before, and
the published keymap does not mention it.

- The block shows the timing the firmware uses (`Hold (600 ms)`, `Double press
  (350 ms)`), read from `/api/meta` rather than restated in the browser, so the
  editor cannot drift from the core.
- The target field follows the chosen action: an entity autocomplete for the device
  actions, a page select for `navigate`/`keymap`, nothing for `none`.
- A key face shows a small **H+D** badge when it carries gestures, so a board full
  of keys is readable at a glance without opening each binding.
- **The conflict rules are surfaced, not enforced**: binding the encoder to a
  gesture, or `adjust` to a matrix key, saves fine but can never fire, so
  `scripts/lint_config.py` warns. Refusing the save was the alternative; a warning
  keeps a configuration that becomes valid on the next firmware revision loadable.

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