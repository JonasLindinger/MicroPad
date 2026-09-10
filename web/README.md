# MicroPad Config Generator — Web-Version

> ⚠️ **AI-assisted:** this tool and the generated Home Assistant automation were
> developed with AI (LLM) assistance, reviewed by the author. Provided as-is, no
> warranty — verify changes on your own setup.

Modern, minimal web UI for configuring a MicroPad (ESP32-S3 Home Assistant controller).
Runs as a Flask app on any Linux host (tested on Ubuntu LXC, also works on a desktop or
the HA host itself) — same functionality as the desktop generator, but in the browser.

## Was die App kann
- **Seiten & Items** definieren (Entities, Typen, Slider, Kategorien)
- **Home Assistant verbinden**: Entities laden, Verbindung testen
- **Automation generieren** (YAML-Vorschau)
- **Upload zu HA** via Config-API (bevorzugt) **oder via SSH**
- **Download** der aktuellen HA-Konfig zurück in die App
- **Aktuelle Seite laden** (liest das MQTT-Last-Action-Sensor-Attribut)
- Enthält den `get_all_pages`-Handler (v2-Firmware): Navigation ohne "Loading..."

## Projektstruktur
```
micropad-web/
├── app/
│   ├── app.py           # Flask-Backend (API + Storage)
│   ├── core.py          # Pure Logik (Page-Payloads, Automation, Validierung)
│   ├── ha_client.py     # HA REST-API + SSH-Client
│   ├── wsgi.py          # gunicorn-Einstieg
│   ├── requirements.txt # flask, pyyaml, gunicorn
│   └── static/          # Frontend (index.html, style.css, app.js)
└── deploy/
    └── setup.sh         # Ubuntu-LXC Install-Skript (systemd)
```

---

## 1. Voraussetzungen (Requirements)

- Python ≥ 3.10 (getestet auf Ubuntu LXC/Server)
- Home Assistant mit MQTT-Integration und Long-Lived-Access-Token
- Optional: SSH-Zugriff auf die HA-Maschine (für den SSH-Upload-Pfad)

## 2. Schnellstart (dev / anywhere)

```bash
cd web
python3 -m venv .venv
.venv/bin/pip install -r app/requirements.txt
MICROPAD_PORT=8080 .venv/bin/python app/app.py
# → http://localhost:8080
```

## 3. Als systemd-Service (Ubuntu LXC / Server)

```bash
# Projekt nach /opt/micropad legen (app/ und deploy/ nebeneinander)
mkdir -p /opt/micropad
cp -r web/app web/deploy /opt/micropad/
bash /opt/micropad/deploy/setup.sh
```
Das Skript installiert venv + Abhängigkeiten + `micropad.service` (gunicorn, Port 8080,
enabled bei Boot, `Restart=always`).

## 4. Aufrufen
```
http://<host-ip>:8080
```

Dokumentation (deutsch/englisch): Installation, Workflow, API-Referenz und
Troubleshooting stehen in `docs/WEB-CONFIG.md` im Projekt-Repository.

---

## Konfiguration (ENV)
| Variable | Default | Zweck |
|---|---|---|
| `MICROPAD_PORT` | `8080` | HTTP-Port |
| `MICROPAD_HOST` | `0.0.0.0` | Bind-Address |
| `MICROPAD_DATA_FILE` | `app/config.json` | Wo Settings+Pages gespeichert werden |

## API-Übersicht
| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/api/config` | Settings + Pages laden |
| `POST` | `/api/config` | Settings/Pages speichern |
| `POST` | `/api/ha/test` | HA-Verbindung testen |
| `POST` | `/api/ha/entities` | Entities von HA laden |
| `POST` | `/api/validate` | Config gegen Firmware-Limits prüfen |
| `POST` | `/api/generate` | Automation YAML erzeugen |
| `POST` | `/api/upload/api` | Upload via HA Config-API |
| `POST` | `/api/upload/ssh` | Upload via SSH (merged) |
| `POST` | `/api/download/api` | Von HA laden (API) |
| `POST` | `/api/download/ssh` | Von HA laden (SSH) |
| `POST` | `/api/load-current-page` | Aktive Pad-Seite lesen |

## Hinweise / Firmware-Abhängigkeiten
- **Max 20 Items/Seite**, Payload-Limit ~1800 B (Firmware-Buffer 2048 B).
- `home`-Seite: unbedingt definieren, sonst hängt das Pad auf "Loading...".
- `number`/`media_player` = Slider (Edit-Modus). `light`/`switch` = Toggle
  (Edit-Modus dort auf dem Pad nicht erreichbar — Firmware-Limit).
- `get_all_pages` wird von der **v2-Firmware** beim Boot angefragt; ohne diesen
  Branch hängt Navigation in "Loading...".