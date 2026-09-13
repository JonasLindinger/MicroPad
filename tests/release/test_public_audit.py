# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Public-release hygiene: secret, placeholder and personal-data audit gate.

The audit over public-tree content must (a) flag every known secret shape, (b)
accept only the APPROVED placeholders via ``release/allowed-public-values.json``,
and (c) detect personal data that must never ship. It fails closed on any
forbidden match, must not false-positive on the synthetic test fixtures
(fake-token / test-token / light.* example entities), and must stay precise
enough that entity-friendly placeholder names pass.

The forbidden fixtures below are assembled from adjacent string fragments so
this test module itself does not contain a contiguous secret-shaped value that a
future scan of the tracked tree would reject.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NOTICE = "AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications."


def _audit():
    import scripts.audit_public_release as m

    return m


AUDIT = _audit()


@pytest.mark.parametrize(  # exact forbidden classes from the integration brief
    ("relative", "content", "code"),
    [
        ("config.json", "{}", "generated-state"),
        ("backup/config.json.bak", "{}", "backup"),
        ("app.py", "token = '" + "eyJ" + "hbGciOiJIUzI1NiJ9." + "a" * 24 + "'", "token"),
        ("deploy/id_ed25519", "-----BEGIN " + "OPENSSH PRIVATE KEY-----", "private-key"),
        ("settings.py", "pass" + "word = '" + "nonpublic-value'", "password"),
        ("firmware/device.ino", "const char* host = \\\"192.168." + "1.42\\\";", "ip-address"),
        ("docs/example.md", "entity: light." + "private-person_bedroom", "personal-data"),
        ("notes.txt", "/ho" + "me/private-user/private/key", "private-path"),
    ],
)
def test_forbidden_public_content_is_reported(tmp_path, relative, content, code):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    findings = AUDIT.audit_tree(tmp_path, forbidden_terms={"private-person", "private-user"})
    assert code in {finding.code for finding in findings}


@pytest.mark.parametrize(
    ("relative", "content"),
    [
        # Approved design placeholders: broker host, port, user, password.
        (
            "firmware/micropad_core.cpp",
            'safeCopy(value.mqttHost, "192.168.0.100");\n'
            "value.mqttPort = 1883;\n"
            'safeCopy(value.mqttUser, "micropad");\n'
            'safeCopy(value.mqttPassword, "replace-me");\n',
        ),
        ("docs/example.md", "password = 'replace-me'  # placeholder"),
        # Entity-friendly names and benign/reserved hosts are not personal data.
        ("tests/browser/test_items.py", "entity_id = 'light.example'\n"),
        ("scripts/deploy.sh", "host='localhost'\n"),
        ("gunicorn.conf.py", 'bind = "0.0.0.0:8080"\n'),
        # The literal repository identifier is allowed only in release docs.
        ("release/integration.md", "repository: JonasLindinger/MicroPad\n"),
        # mDNS / HA demo host uses no credentials and a benign TLD.
        ("config.example.json", '{"ha_url": "http://homeassistant.local:8123"}'),
    ],
)
def test_approved_public_values_are_allowed(tmp_path, relative, content):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    findings = AUDIT.audit_tree(tmp_path, forbidden_terms=set())
    assert [(f.code, f.path, f.line) for f in findings] == []


def test_synthetic_test_fixtures_are_not_flagged(tmp_path):
    files = {
        "tests/integration/test_roundtrip.py": (
            'config.settings.ha_token = "fake-token"\n'
            'config.settings.ha_token = "test-token"\n'
        ),
        "tests/browser/test_entities.py": (
            "entity_id = 'light.desk_lamp'\n"
            "entity_id = 'light.living_room'\n"
            "entity = 'light.desk'\n"
        ),
        "tests/fakes/fake_ha.py": 'return authorization.startswith("Bearer ")\n',
    }
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    findings = AUDIT.audit_tree(tmp_path, forbidden_terms=set())
    assert [(f.code, f.path, f.line) for f in findings] == []


@pytest.mark.parametrize(
    ("relative", "content", "code"),
    [
        ("env.py", "x = '" + "AKIA" + "1234567890ABCDEF'\n", "token"),
        ("cfg.py", "c = '-----BEGIN " + "RSA PRIVATE KEY-----'\n", "private-key"),
        ("port.py", "mqtt://user:" + "pass@127.0.0.1\n", "credential-url"),
        ("data.txt", "192.168." + "50.203\n", "ip-address"),
        ("defs.yaml", "password: " + "hunter2\n", "password"),
    ],
)
def test_any_secret_shaped_value_outside_allowance_is_reported(tmp_path, relative, content, code):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    findings = AUDIT.audit_tree(tmp_path, forbidden_terms=set())
    assert code in {finding.code for finding in findings}


@pytest.mark.parametrize(
    ("relative", "content", "code"),
    [
        (f"pcb/koa{idx}.kicad_pcb", line, "none")
        for idx, line in enumerate(
            [
                "(at 106.295 116.98)",
                "(start 141.895 102.97)",
                "(at 183.642 116.078 0)",
                "(xy 87.595 53.77) (xy 177.595 150.77)",
                "(end 163 102.175)",
                "(at 124.714 127 0)",
                "(uuid \"ff0d8899-4b83-4d97-a419-" + "8029399ef6e8\")",
                "(uuid \"5e9223f1-455a-4616-b531-" + "3692822ad08f\")",
                "(uuid \"140be295-caa5-4a98-a365-" + "9233383d2bff\")",
                "(uuid \"c4d974e0-bbed-468a-a658-" + "7640411b083f\")",
                "(uuid \"94e0c064-c07c-40e2-a210-" + "5799954b52dc\")",
            ]
        )
    ],
)
def test_kicad_numeric_data_is_not_misread_as_phones(tmp_path, relative, content, code):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    findings = AUDIT.audit_tree(tmp_path, forbidden_terms=set())
    assert [(f.code, f.path, f.line) for f in findings] == []


@pytest.mark.parametrize(
    "content",
    [
        "call " + "123-456-" + "7890 please",
        "reach " + "555 123 " + "4567",
        "phone " + "123.456." + "7890",
        "mixed " + "123-456 " + "7890",
        "mixed " + "123 456-" + "7890",
        "mixed " + "123/456-" + "7890",
        "mixed " + "123.456 " + "7890",
        "parenthesized " + "(123) 456-" + "7890",
        "international parenthesized +" + "1 (123) 456-" + "7890",
        "contiguous final groups " + "123-" + "4567890",
        "fax +" + "491701234567",
    ],
)
def test_real_phones_are_still_reported(tmp_path, content):
    path = tmp_path / "notes.txt"
    path.write_text(content, encoding="utf-8")
    findings = AUDIT.audit_tree(tmp_path, forbidden_terms=set())
    assert "personal-data" in {finding.code for finding in findings}


def test_audit_scans_the_generated_public_tree_clean(tmp_path):
    """End-to-end: export the exact software manifest and prove the gate passes."""
    manifest = (ROOT / "release" / "software-files.txt").read_text(encoding="utf-8").splitlines()
    for relative in manifest:
        src = ROOT / relative
        dst = tmp_path / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
    findings = AUDIT.audit_tree(
        tmp_path, forbidden_terms=AUDIT.derive_forbidden_terms(ROOT)
    )
    assert [(f.code, f.path, f.line) for f in findings] == []


def test_candidate_files_scanned_excludes_git_metadata(tmp_path):
    (tmp_path / "README.md").write_text("synthetic public tree\n", encoding="utf-8")
    (tmp_path / ".git" / "objects").mkdir(parents=True)
    (tmp_path / ".git" / "objects" / "metadata").write_text("internal\n", encoding="utf-8")
    output = tmp_path.parent / "audit.json"

    assert AUDIT.main(["--tree", str(tmp_path), "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["files_scanned"] == 1


# --- explicit software manifest ---------------------------------------------------


def test_manifest_is_sorted_has_no_globs_and_no_blank_lines():
    lines = (ROOT / "release" / "software-files.txt").read_text(encoding="utf-8").split("\n")
    assert lines[-1] == ""  # exactly one trailing newline
    body = [ln for ln in lines if ln != ""]
    assert all(ln.strip() == ln and "*" not in ln and "[" not in ln for ln in body)
    assert sorted(body, key=lambda s: s.encode("utf-8")) == body


def test_manifest_lists_every_tracked_file_outside_internal_exclusions_exactly_once():
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    excluded_prefixes = ("docs/superpowers/",)
    excluded_exact = {"release/software-files.txt"}  # the manifest itself is clean-room metadata
    expected = sorted(
        f for f in tracked if not f.startswith(excluded_prefixes) and f not in excluded_exact
    )
    manifest = (
        (ROOT / "release" / "software-files.txt").read_text(encoding="utf-8").splitlines()
    )
    assert manifest == expected
    assert len(manifest) == len(set(manifest))


def test_manifest_paths_exist_and_no_excluded_basename_appears():
    manifest = [
        ln for ln in (ROOT / "release" / "software-files.txt").read_text(encoding="utf-8").splitlines()
        if ln != ""
    ]
    reject_basename = {"config.json", ".env", "id_rsa", "id_ed25519", "known_hosts"}
    reject_suffix = {".bak", ".backup", ".orig", ".pem", ".key", ".pyc"}
    reject_dir_prefix = (
        ".venv/", "venv/", "__pycache__/", ".pytest_cache/", "node_modules/",
        "playwright-report/", "test-results/", "build/", ".arduino/",
        "docs/superpowers/", ".git/",
    )
    for relative in manifest:
        assert (ROOT / relative).is_file(), f"manifest path missing: {relative}"
        assert relative not in reject_basename and Path(relative).name not in reject_basename
        assert Path(relative).suffix.lower() not in reject_suffix
        assert not relative.startswith(reject_dir_prefix)


# --- ignore rules -----------------------------------------------------------------


def test_gitignore_keeps_local_state_out_of_any_release():
    for relative in ("config.json", "build/live-ha-preview/release.sha256", "release/local-hardware-acceptance.json"):
        out = subprocess.run(
            ["git", "-C", str(ROOT), "check-ignore", "-v", relative],
            capture_output=True,
            text=True,
        )
        assert out.returncode == 0, f"{relative} is not ignored:\n{out.stdout}{out.stderr}"
        assert relative in out.stdout