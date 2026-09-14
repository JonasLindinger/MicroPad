# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
import json
from pathlib import Path

import micropad
from micropad.constants import AI_NOTICE, KEY_IDS


def test_package_exposes_version_notice_and_fourteen_keys() -> None:
    assert micropad.__version__ == "0.1.0"
    assert AI_NOTICE.startswith("AI-assisted development")
    assert KEY_IDS == tuple(
        [f"r{row}c{column}" for row in range(3) for column in range(4)] + ["enc_up", "enc_down"]
    )


def test_local_state_is_ignored() -> None:
    ignored = Path(".gitignore").read_text(encoding="utf-8").splitlines()
    # Anchored (repo-root) forms, matching the stricter public-release rules.
    assert "/config.json" in ignored
    assert "/.venv/" in ignored


def _is_generated_or_hidden(path: Path) -> bool:
    return any(
        part == "__pycache__" or part.endswith(".egg-info") or part.startswith(".")
        for part in path.parts
    )


def tracked_project_text_paths() -> list[Path]:
    roots = [Path(name) for name in ("src", "tests", "scripts", "deploy", "homeassistant")]
    paths = {
        path
        for root in roots
        if root.exists()
        for path in root.rglob("*")
        if path.is_file() and not _is_generated_or_hidden(path)
    }
    paths.update(
        path
        for pattern in ("*.toml", "*.txt", "*.py")
        for path in Path(".").glob(pattern)
        # .coverage and requirements-lock.txt are machine-generated (uv export
        # --frozen), not authored, so they carry no AI notice by design.
        if path.name not in (".coverage", "requirements-lock.txt")
    )
    paths.add(Path("config.example.json"))
    return sorted(paths)


def test_every_source_and_documentation_file_carries_ai_notice() -> None:
    paths = tracked_project_text_paths()
    missing = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            if json.loads(text).get("_ai_assisted_notice") != AI_NOTICE:
                missing.append(str(path))
        elif AI_NOTICE not in text:
            missing.append(str(path))
    assert missing == []
