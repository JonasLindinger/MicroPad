"""MicroPad Config Generator - web backend.

NOTE: developed with AI (LLM) assistance, reviewed by the author; provided
as-is, without warranty. See the project README for the full AI disclaimer.

Serves a clean, single-page UI to configure the MicroPad:
  * define pages/items (entities, types, sliders, categories)
  * connect to Home Assistant (entities fetch, test)
  * generate automation YAML
  * upload to HA via Config API (preferred) or SSH
  * download current config from HA
  * load the currently active page from HA

Storage: a single JSON file per user (no database dependency).
"""

import os
import gzip
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from core import (build_ha_automation_config, build_automation_dict,
                  generate_page_payload, generate_all_pages_payload,
                  generate_automation_yaml, validate_pages,
                  parse_automation_to_pages, ITEM_TYPES,
                  generate_keymap_payload, normalize_keymap,
                  effective_keymap_for_page, KEYMAP_KEYS, KEY_ACTION_TYPES,
                  DEFAULT_KEYMAP)
from ha_client import HAClient, SSHClient, HAClientError

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_FILE = Path(os.environ.get("MICROPAD_DATA_FILE", BASE_DIR / "config.json"))

DEFAULT_SETTINGS = {
    "ha_url": "http://192.168.178.17:8123",
    "ha_token": "",
    "mqtt_broker": "192.168.178.17",
    "ssh_host": "192.168.178.17",
    "ssh_user": "root",
    "ssh_key": "/root/.ssh/id_ed25519",
    "remote_path": "/srv/homeassistant/automations.yaml",
}

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/healthz")
def healthz():
    # Cheap liveness probe for a load balancer or uptime check. Returns the
    # current unix timestamp so callers can spot stale caches vs. an actual
    # outage (the body changes on every hit).
    return jsonify({"ok": True, "ts": int(time.time()),
                    "iso": datetime.now(timezone.utc).isoformat()})


# ---------------------------------------------------------------------------
# Static assets: gzip + long cache lifetime
# ---------------------------------------------------------------------------
# index.html is served no-cache (asset versions are bumped with ?v=N in the
# query string), so everything under /static can be cached "immutable".
_GZIP_TYPES = ("text/", "application/javascript", "application/json")


@app.after_request
def _compress_and_cache(resp):
    if request.path.startswith("/static"):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    if (request.path.startswith("/static")
            and resp.status_code == 200
            and "gzip" in request.headers.get("Accept-Encoding", "")
            and resp.mimetype.startswith(_GZIP_TYPES)):
        # send_from_directory streams the file (direct passthrough), so
        # get_data() raises RuntimeError there -> read the raw payload.
        try:
            data = resp.get_data()
        except RuntimeError:
            data = b"".join(resp.response)
        if len(data) > 500:
            data = gzip.compress(data)
            resp.set_data(data)
            resp.headers["Content-Encoding"] = "gzip"
            resp.headers["Content-Length"] = str(len(data))
            resp.headers["Vary"] = "Accept-Encoding"
    return resp


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def load_data():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"settings": dict(DEFAULT_SETTINGS), "pages": [],
            "entities": [], "keymap": {}}


def save_data(settings, pages, entities=None, keymap=None):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {"settings": settings, "pages": pages}
    if entities is not None:
        data["entities"] = entities
    if keymap is not None:
        data["keymap"] = keymap
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                         encoding="utf-8")
    try:
        os.chmod(DATA_FILE, 0o600)
    except Exception:
        pass


def _client(settings):
    return HAClient(settings.get("ha_url"), settings.get("ha_token"))


# ---------------------------------------------------------------------------
# Pages / app
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    # Serve the shell with no-cache so the browser always picks up the current
    # asset versions (?v=N on style.css/app.js). Without this, a stale cached
    # index.html keeps pointing at the previous JS and new UI elements appear
    # dead until the user does a hard reload.
    resp = send_from_directory(STATIC_DIR, "index.html")
    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


@app.get("/api/meta")
def meta():
    return jsonify({"item_types": ITEM_TYPES,
                    "keymap_keys": KEYMAP_KEYS,
                    "key_actions": KEY_ACTION_TYPES,
                    "keymap_defaults": DEFAULT_KEYMAP})


@app.get("/api/config")
def get_config():
    data = load_data()
    return jsonify(data)


@app.post("/api/config")
def post_config():
    body = request.get_json(silent=True) or {}
    data = load_data()
    if "settings" in body:
        data["settings"] = {**data["settings"], **body["settings"]}
    if "pages" in body:
        data["pages"] = body["pages"]
    if "entities" in body:
        data["entities"] = body["entities"]
    if "keymap" in body:
        data["keymap"] = body["keymap"]
    save_data(data["settings"], data["pages"], data.get("entities"),
              data.get("keymap"))
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# HA actions
# ---------------------------------------------------------------------------
@app.post("/api/ha/test")
def ha_test():
    data = load_data()
    c = _client(data["settings"])
    try:
        res = c.test_connection()
        return jsonify({"ok": True, "message": f"Connected - Home Assistant {res.get('version', '?')}"})
    except HAClientError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.post("/api/ha/entities")
def ha_entities():
    data = load_data()
    c = _client(data["settings"])
    try:
        entities = c.fetch_entities()
        data["entities"] = entities
        save_data(data["settings"], data.get("pages", []), entities,
                  data.get("keymap"))
        return jsonify({"ok": True, "entities": entities, "count": len(entities)})
    except HAClientError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.post("/api/validate")
def _validate():
    body = request.get_json(silent=True) or {}
    pages = body.get("pages", [])
    errors, warnings = validate_pages(pages)
    return jsonify({"ok": not errors, "errors": errors, "warnings": warnings})


@app.post("/api/generate")
def generate():
    body = request.get_json(silent=True) or {}
    pages = body.get("pages", [])
    keymap = body.get("keymap")
    errors, warnings = validate_pages(pages)
    if errors:
        return jsonify({"ok": False, "errors": errors, "warnings": warnings}), 400
    automation = build_automation_dict(pages, keymap)
    automation["id"] = "micropad_controller"
    api_config = build_ha_automation_config(pages, keymap)
    home = next((p for p in pages if p["id"] == "home"), pages[0] if pages else None)
    return jsonify({
        "ok": True,
        "errors": errors,
        "warnings": warnings,
        "yaml_automations": generate_automation_yaml(pages, keymap),
        "keymap_payload": generate_keymap_payload(
            effective_keymap_for_page(pages, home["id"], keymap) if home else keymap),
        "api_config": api_config,
        "all_pages_payload": generate_all_pages_payload(pages) if pages else "",
        "home_page_payload": generate_page_payload(home) if home else "",
    })


@app.post("/api/upload/api")
def upload_api():
    data = load_data()
    settings = data["settings"]
    body = request.get_json(silent=True) or {}
    pages = body.get("pages", [])
    keymap = body.get("keymap")
    errors, _warnings = validate_pages(pages)
    if errors:
        return jsonify({"ok": False, "error": "Config invalid:\n" + "\n".join(errors)}), 400

    c = _client(settings)
    try:
        config = build_ha_automation_config(pages, keymap)
        c.put_automation_config(config)
        c.reload_automations()
        try:
            c.push_mqtt_sensor()
        except HAClientError:
            pass
        home = next((p for p in pages if p["id"] == "home"), pages[0] if pages else None)
        if home:
            c.publish_page(generate_page_payload(home))
        # Push the home page's key map straight away (retained), so re-bound
        # keys work without waiting for the pad's next connect. Per-page maps
        # are served by the automation on every navigate.
        try:
            c.publish_keymap(generate_keymap_payload(
                effective_keymap_for_page(pages, home["id"], keymap)))
        except HAClientError:
            pass
        # Republish the full page catalog (retained) so the pad's cache can
        # never go stale after a config change - otherwise navigation can
        # show an outdated page (wrong first item on a category) until the
        # next reboot. Requires firmware with an 8 KB MQTT buffer (catalog
        # is ~6 KB); a too-small buffer truncates the JSON silently.
        try:
            c.publish_all_pages(generate_all_pages_payload(pages))
        except HAClientError:
            pass
        return jsonify({"ok": True,
                        "message": "Uploaded via Config API, reloaded automations, "
                                   "published the home page, key map and page catalog to the pad."})
    except HAClientError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.post("/api/upload/ssh")
def upload_ssh():
    data = load_data()
    settings = data["settings"]
    body = request.get_json(silent=True) or {}
    pages = body.get("pages", [])
    keymap = body.get("keymap")
    errors, _warnings = validate_pages(pages)
    if errors:
        return jsonify({"ok": False, "error": "Config invalid:\n" + "\n".join(errors)}), 400

    remote_path = settings.get("remote_path") or ""
    # never overwrite configuration.yaml by accident
    if remote_path.endswith("configuration.yaml"):
        remote_path = remote_path.replace("configuration.yaml", "automations.yaml")

    try:
        ssh = SSHClient(settings.get("ssh_host"), settings.get("ssh_user"),
                        settings.get("ssh_key"), remote_path)
        existing = ssh.read_remote()
        new_automation = build_automation_dict(pages, keymap)
        new_automation["id"] = "micropad_controller"
        merged = merge_automation_yaml(existing, new_automation, "micropad_controller")
        tmp = Path(tempfile.mkstemp(suffix=".yaml", prefix="micropad_")[1])
        tmp.write_text(merged, encoding="utf-8")
        try:
            ssh.write_remote(tmp)
        finally:
            tmp.unlink(missing_ok=True)

        # reload via API if we have token, else leave a note
        if settings.get("ha_token"):
            try:
                _client(settings).reload_automations()
                home = next((p for p in pages if p["id"] == "home"), pages[0] if pages else None)
                if home:
                    _client(settings).publish_page(generate_page_payload(home))
                try:
                    _client(settings).publish_keymap(generate_keymap_payload(
                        effective_keymap_for_page(pages, home["id"], keymap)))
                except HAClientError:
                    pass
                message = ("Merged + uploaded via SSH, reloaded automations via API, "
                           "published home page.")
            except HAClientError:
                message = ("Merged + uploaded via SSH. Could not reload via API - "
                           "run: systemctl restart homeassistant")
        else:
            message = ("Merged + uploaded via SSH. No HA token set for reload - "
                       "run: systemctl restart homeassistant")
        return jsonify({"ok": True, "message": message})
    except HAClientError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.post("/api/download/api")
def download_api():
    data = load_data()
    c = _client(data["settings"])
    try:
        config = c.get_automation_config()
        pages = parse_automation_to_pages(config)
        return jsonify({"ok": True, "pages": pages})
    except HAClientError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.post("/api/download/ssh")
def download_ssh():
    data = load_data()
    settings = data["settings"]
    remote_path = settings.get("remote_path") or ""
    if remote_path.endswith("configuration.yaml"):
        remote_path = remote_path.replace("configuration.yaml", "automations.yaml")
    try:
        ssh = SSHClient(settings.get("ssh_host"), settings.get("ssh_user"),
                        settings.get("ssh_key"), remote_path)
        text = ssh.read_remote()
        if not text.strip():
            return jsonify({"ok": False, "error": "Remote file is empty."}), 400
        import yaml
        parsed = yaml.safe_load(text)
        entry = None
        if isinstance(parsed, list):
            entry = next((a for a in parsed if isinstance(a, dict)
                          and a.get("id") == "micropad_controller"), None)
        elif isinstance(parsed, dict):
            entry = parsed.get("automation")
        if not entry:
            return jsonify({"ok": False, "error": "No 'micropad_controller' automation found."}), 400
        pages = parse_automation_to_pages(entry)
        return jsonify({"ok": True, "pages": pages})
    except HAClientError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.post("/api/load-current-page")
def load_current_page():
    data = load_data()
    c = _client(data["settings"])
    try:
        state = c.current_page_sensor()
        if state is None:
            return jsonify({"ok": False, "error": "Sensor 'sensor.micropad_last_action' not found. ",
                            "hint": "Upload via API first so the MQTT sensor is created."}), 404
        attrs = state.get("attributes", {})
        return jsonify({
            "ok": True,
            "state": state.get("state", ""),
            "page_id": attrs.get("page_id", ""),
            "target_page": attrs.get("target_page", ""),
            "attributes": attrs,
        })
    except HAClientError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


# ---------------------------------------------------------------------------
# YAML merge helper (shared by SSH upload)
# ---------------------------------------------------------------------------
def _yaml_str_representer(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


def merge_automation_yaml(existing_text, new_automation, automation_id):
    import yaml
    yaml.add_representer(str, _yaml_str_representer)
    existing = []
    if existing_text.strip():
        try:
            parsed = yaml.safe_load(existing_text)
            if isinstance(parsed, list):
                existing = parsed
            elif isinstance(parsed, dict) and "automation" in parsed:
                existing = parsed.get("automation") or []
        except Exception as e:
            raise HAClientError(
                f"Existing remote file could not be parsed as YAML, refusing to touch it:\n{e}"
            )
    merged = [a for a in existing if isinstance(a, dict) and a.get("id") != automation_id]
    merged.append(new_automation)
    return yaml.dump(merged, sort_keys=False, allow_unicode=True, width=1000)


# NOTE: do NOT define a local generate_automation_yaml() here. A leftover copy
# used to shadow the one imported from core (same name, older signature), so
# the keymap argument never reached the real implementation.


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    host = os.environ.get("MICROPAD_HOST", "0.0.0.0")
    port = int(os.environ.get("MICROPAD_PORT", "8080"))
    app.run(host=host, port=port, debug=bool(os.environ.get("MICROPAD_DEBUG")))