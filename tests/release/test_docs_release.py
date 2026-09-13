# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Release documentation coverage and AI-notice enforcement tests.

Aligns with ``tests/test_foundation.py`` (which audits the tracked source/docs it
enumerates) by exercising the independent ``scripts/audit_notices.py`` tool over the
release documentation set here. Together the two cover every tracked path without
double-reporting: the foundation test owns its narrower rooted scan; this module owns the
exact release docs plus a deliberate-failure probe against a temporary unnoticed file.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOTICE = "AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications."
DOCS = {
    "README.md": ["Safety", "Quick start", "Firmware", "Home Assistant", "Configurator"],
    "docs/firmware.md": ["Pin map", "Build", "Flash", "Setup portal", "Sleep"],
    "docs/home-assistant.md": ["MQTT contract", "API upload", "SSH upload", "Rollback"],
    "docs/configurator.md": ["Pages editor", "Key editor", "Entity discovery", "Validation"],
    "docs/deployment.md": ["Install", "Update", "Logs", "Uninstall"],
    "docs/hardware-acceptance.md": ["Equipment", "Power and sleep", "Input", "Display", "Connectivity"],
    "docs/security-release.md": ["Secrets", "Approval gates", "Public release", "Incident response"],
}


def test_release_docs_are_english_complete_and_noticed():
    for relative, headings in DOCS.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert NOTICE in text
        assert all(f"# {heading}" in text or f"## {heading}" in text for heading in headings)
        assert "192.168.0.100" in text or relative not in {"README.md", "docs/firmware.md"}


def test_release_docs_contain_no_german_ui_phrases() -> None:
    bad = (" Einstellungen", " Zurück", " Startseite", " Gerät", " Verbindung")
    files = [Path("README.md"), *Path("docs").glob("*.md")]
    hits = [
        f"{path}: {word}"
        for path in files
        for word in bad
        if word in path.read_text(encoding="utf-8")
    ]
    assert hits == []


def _load_audit() -> object:
    spec = importlib.util.spec_from_file_location(
        "audit_notices", ROOT / "scripts" / "audit_notices.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_audit_notices_returns_path_for_unnoticed_file(tmp_path: Path) -> None:
    audit = _load_audit()
    unnoticed = tmp_path / "sample.py"
    unnoticed.write_text("print('no notice here')\n", encoding="utf-8")
    assert audit.audit_notices([unnoticed]) == [str(unnoticed)]


def test_audit_notices_is_empty_for_noticed_file(tmp_path: Path) -> None:
    audit = _load_audit()
    noticed = tmp_path / "sample.py"
    noticed.write_text(f"# {NOTICE}\nprint('ok')\n", encoding="utf-8")
    assert audit.audit_notices([noticed]) == []


def test_audit_coverage_of_release_docs_is_complete() -> None:
    audit = _load_audit()
    doc_paths = [ROOT / "README.md", *(ROOT / "docs").glob("*.md"), ROOT / "scripts" / "audit_notices.py"]
    assert audit.audit_notices([path.relative_to(ROOT) for path in sorted(doc_paths)]) == []