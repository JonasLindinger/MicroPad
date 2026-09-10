# MicroPad — Web Config Generator

A self-hosted **web app** for configuring everything Home Assistant-side: pages, items,
entities, and the automation that talks to the MicroPad. No Arduino IDE required — the
pad's firmware stays static; you manage the menu from the browser.

> ⚠️ **AI-assisted:** the app and the automations it generates were developed with AI
> (LLM) assistance, reviewed by the author. As-is, no warranty — verify changes.

---

## 1. What it does

- **Pages & items editor** — build the pad's hierarchy (home → sub-pages → items)
- **Live HA entity search** — pick entities from a searchable list showing friendly name
  + entity id (so you don't have to type `light.living_room` by hand)
- **Item types** — category, light, switch, script, button, sensor, media_player,
  number, settings, back
- **Slider values** — min/max/step + editable flag for `number` / `media_player`
- **Generator** — produces the automation YAML, including the boot-time `get_all_pages`
  catalog handler
- **Validation** — checks against firmware limits (max 20 items/page, ~1800 B payload,
  `home` page must exist, `number.*` vs `input_number.*` service split) before upload
- **Upload to HA** — two paths:
  - *API* (preferred): HA Config API creates/updates `automation.micropad_controller` + 
    MQTT sensor, reloads automations, publishes the home page
  - *SSH* (fallback): merges into the existing `automations.yaml`, keeps other
    automations intact, reloads via API or restarts HA
- **Download from HA** — pull the live automation back into the app (API or SSH)
- **Load current page** — reads the pad's last action/page from HA and selects it

## 2. Files (in this repo: `web/`)

```
web/
├── app/
│   ├── app.py           # Flask backend (REST API + JSON storage)
│   ├── core.py          # pure logic: payloads, automation builder, validation
│   ├── ha_client.py     # HA REST API + SSH/scp client
│   ├── wsgi.py          # gunicorn entrypoint
│   ├── requirements.txt # flask, pyyaml, gunicorn
│   └── static/          # frontend (index.html, style.css, app.js)
├── deploy/
│   └── setup.sh         # Ubuntu LXC installer (systemd unit, gunicorn)
└── README.md
```

## 3. Requirements

- Python ≥ 3.10 on any Linux host (tested: Ubuntu server LXC, but a desktop or the HA
  host works too)
- A Home Assistant instance with:
  - **HA URL + Long-Lived Access Token** (Profile → Security → Tokens)
  - **MQTT integration** (the pad talks pure MQTT; the automation uses `mqtt.publish`)
- Optional for the SSH upload path: SSH access to the HA host + a private key

## 4. Running it

### Quick (dev, any machine)

```sh
cd web
python3 -m venv .venv
.venv/bin/pip install -r app/requirements.txt
MICROPAD_PORT=8080 .venv/bin/python app/app.py
# → http://localhost:8080
```

### As a systemd service (Ubuntu LXC / server)

```sh
# put the repo at /opt/micropad so deploy/setup.sh finds web/app
cp -r web /opt/micropad/
bash /opt/micropad/deploy/setup.sh
```
This installs a venv + gunicorn + `micropad.service` (enabled at boot, `Restart=always`),
listening on **port 8080** (override with `MICROPAD_PORT`).

Data (HA settings + pages) is stored in `app/config.json` (chmod 600).

## 5. First use / workflow

1. Open `http://<host>:8080` — the **Einstellungen** (settings) drawer is top-left ⚙.
2. Fill in:
   - **HA URL** e.g. `http://192.168.0.100:8123` (or `homeassistant.local:8123`)
   - **Access Token** (long-lived)
   - **MQTT Broker** (the pad's broker, usually the HA host)
   - SSH fields only for the SSH upload path
   → **Speichern** (save).
3. **Verbindung testen** → should show the HA version. Then **Entities laden**.
4. Create the **`home` page** (required!) plus any sub-pages; add items with the
   searchable entity picker.
5. **Generieren** → preview the YAML (optional).
6. **Zu HA hochladen ▸** → API path by default. The app:
   - writes `automation.micropad_controller` via the Config API,
   - reloads automations (no HA restart),
   - publishes the home page to the pad.
7. The MicroPad picks it up. If the pad is asleep, wake it once — it re-fetches pages.

## 6. Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `MICROPAD_PORT` | `8080` | HTTP port |
| `MICROPAD_HOST` | `0.0.0.0` | bind address |
| `MICROPAD_DATA_FILE` | `app/config.json` | storage path |

## 7. API reference (if you want to script it)

| Method | Path | Description |
|---|---|---|
| GET | `/api/config` | settings + pages |
| POST | `/api/config` | save settings/pages/entities |
| POST | `/api/ha/test` | test HA connection |
| POST | `/api/ha/entities` | fetch entities (also caches them) |
| POST | `/api/validate` | run firmware-limit validation |
| POST | `/api/generate` | build automation YAML + payloads |
| POST | `/api/upload/api` | upload via HA Config API + reload |
| POST | `/api/upload/ssh` | merge + scp into automations.yaml |
| POST | `/api/download/api` | read automation back from HA |
| POST | `/api/download/ssh` | read automation back via SSH |
| POST | `/api/load-current-page` | read pad's current page from HA |

## 8. Security notes

- The app is unauthenticated — it is meant for a **trusted LAN** only. Do not expose it
  directly to the internet. Put it behind a reverse proxy with auth if you need remote
  access.
- The HA token is stored in plaintext in `config.json` (0600). Protect the host
  accordingly.
- SSH path uses your key — prefer the API upload where possible.

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| Upload fails with `Config API not available` | Enable the **Config** integration in HA, or use the SSH upload path |
| Auth 401 | Regenerate the long-lived token; check HA URL is `http://<ip>:8123` |
| Entities list empty in the item dialog | Click **Entities laden** in settings or the inline link in the dialog |
| Pad stays on old menu after upload | Wake the pad (it fetches pages on wake/login), or press Home |
| Old browser shows old UI | Hard refresh (Ctrl+Shift+R) |

See also [FIRMWARE.md](FIRMWARE.md) for the MQTT protocol and [HARDWARE.md](HARDWARE.md)
for the pinout the firmware expects.