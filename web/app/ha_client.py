"""MicroPad Config Generator — Home Assistant client.

Handles HA REST API calls (entities, config upload/download, reload) and
optional SSH-based upload/download of automations.yaml to the HA box.
"""

import json
import subprocess
import urllib.error
import urllib.request


class HAClientError(Exception):
    pass


class HAClient:
    def __init__(self, url, token):
        self.url = (url or "").rstrip("/")
        self.token = token or ""

    # -- low level ---------------------------------------------------------
    def request(self, path, method="GET", payload=None):
        if not self.url or not self.token:
            raise HAClientError("HA URL and access token are required (see Settings).")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.url + path, data=data,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            raise HAClientError(f"HTTP {e.code}: {e.reason}{(' - ' + body) if body else ''}")
        except urllib.error.URLError as e:
            raise HAClientError(f"Could not reach {self.url}: {e.reason}")

    # -- info --------------------------------------------------------------
    def test_connection(self):
        return self.request("/api/")

    def fetch_entities(self):
        res = self.request("/api/states")
        # return list of {entity_id, name}
        out = []
        for e in res:
            attrs = e.get("attributes", {}) or {}
            out.append({
                "entity_id": e["entity_id"],
                "name": attrs.get("friendly_name", ""),
                "state": e.get("state", ""),
                "domain": e["entity_id"].split(".")[0],
            })
        out.sort(key=lambda e: e["entity_id"])
        return out

    # -- automation config -------------------------------------------------
    def get_automation_config(self, automation_id="micropad_controller"):
        return self.request(f"/api/config/automation/config/{automation_id}")

    def delete_automation_config(self, automation_id="micropad_controller"):
        return self.request(f"/api/config/automation/config/{automation_id}", method="DELETE")

    def put_automation_config(self, config):
        aid = config.get("id", "micropad_controller")
        try:
            return self.request(f"/api/config/automation/config/{aid}", method="POST", payload=config)
        except HAClientError as e:
            if "HTTP 404" in str(e):
                raise HAClientError(
                    "Config API not available. Enable the 'Config' integration, or use "
                    "the SSH upload path instead."
                )
            raise

    def reload_automations(self):
        return self.request("/api/services/automation/reload", method="POST", payload={})

    # -- mqtt helpers ------------------------------------------------------
    def publish_page(self, page_payload, retain=True):
        return self.request("/api/services/mqtt/publish", method="POST", payload={
            "topic": "micropad/page/current",
            "payload": page_payload,
            "retain": retain,
            "qos": 1,
        })

    def push_mqtt_sensor(self):
        # MQTT Last-Action sensor used by "Load current page from HA".
        payload = {
            "state_topic": "micropad/event",
            "value_template": "{{ value_json.action }}",
            "json_attributes_topic": "micropad/event",
            "name": "MicroPad Last Action",
            "unique_id": "micropad_last_action",
        }
        try:
            self.request("/api/config/mqtt/config/micropad_last_action",
                         method="POST", payload=payload)
            self.request("/api/services/mqtt/reload", method="POST", payload={})
        except HAClientError:
            pass  # optional

    def current_page_sensor(self):
        try:
            return self.request("/api/states/sensor.micropad_last_action")
        except HAClientError as e:
            if "HTTP 404" in str(e):
                return None
            raise



# ---------------------------------------------------------------------------
# SSH-based upload / download
# ---------------------------------------------------------------------------
class SSHClient:
    """Talks to the HA box over SSH (scp + ssh), used by the fallback
    upload/download path that merges into the existing automations.yaml."""

    def __init__(self, host, user, key_path, remote_path):
        if not host or not user or not key_path or not remote_path:
            raise HAClientError("SSH host, user, key path and remote path are required (see Settings).")
        self.host = host
        self.user = user
        self.key = key_path
        self.remote = remote_path

    def _base(self, extra=None):
        cmd = [
            "ssh", "-i", self.key,
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            f"{self.user}@{self.host}",
        ]
        if extra:
            cmd += extra
        return cmd

    def read_remote(self):
        """Return current remote file text, or '' if it doesn't exist yet."""
        cmd = self._base([f"cat '{self.remote.replace(chr(39), '')}' 2>/dev/null || true"])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise HAClientError(f"SSH read failed: {r.stderr.strip() or r.stdout.strip()}")
        return r.stdout

    def write_remote(self, local_path):
        cmd = [
            "scp", "-i", self.key,
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "BatchMode=yes",
            str(local_path),
            f"{self.user}@{self.host}:{self.remote}",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise HAClientError(f"SCP failed: {r.stderr.strip() or r.stdout.strip()}")
        return True

    def authed(self):
        """Return True if the SSH credentials actually work."""
        r = subprocess.run(self._base(["echo ok"]), capture_output=True, text=True, timeout=15)
        return r.returncode == 0