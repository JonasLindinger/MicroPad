#!/usr/bin/env python3
# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Audit every tracked doc/source file for the exact AI-assistance notice.

The notice must appear verbatim in every authored file that is documentation or
source (see the extension list below). JSON schemas and JSON data are excluded:
JSON has no comment syntax and is machine-readable data, not source/documentation.

Exit 0 when every audited file carries the exact ``AI_NOTICE`` literal; exit 1 and
list only the offending paths (never file contents) otherwise.

Usage:
    scripts/audit_notices.py                 # all tracked files matching the extensions
    scripts/audit_notices.py --path README.md --path scripts/audit_notices.py
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

# The mandated `python3 scripts/audit_notices.py` runs without a pre-installed
# package: add the checkout-local src/ so `micropad.constants` resolves.
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))

from micropad.constants import AI_NOTICE  # noqa: E402

# Set of file extensions that must carry the notice. JSON is deliberately absent.
NOTICE_EXTENSIONS = {
    ".md", ".py", ".sh", ".yaml", ".yml", ".service", ".conf",
    ".ino", ".h", ".hpp", ".c", ".cpp", ".js", ".css", ".html",
}


def audit_notices(paths: Sequence[Path]) -> list[str]:
    """Return the paths that do not contain the exact notice; empty means full coverage.

    A path that cannot be read is treated as a missing file and reported, so the
    audit fails closed rather than silently passing.
    """
    missing: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            missing.append(str(path))
            continue
        if AI_NOTICE not in text:
            missing.append(str(path))
    return missing


def tracked_extension_paths() -> list[Path]:
    """Return tracked files under the repo root whose suffix is in NOTICE_EXTENSIONS."""
    # S603/S607: argv is a fixed literal immutable list (never a shell string and
    # never user-controlled), so this is safe; fail closed if git itself errors.
    argv = ["git", "-C", str(_REPO), "ls-files", "-z"]
    output = subprocess.run(argv, check=True, capture_output=True, text=True)
    return [
        Path(entry)
        for entry in output.stdout.split("\0")
        if entry and Path(entry).suffix in NOTICE_EXTENSIONS
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "additional (e.g. untracked) path to audit; repeatable. Combine with the "
            "default tracked-file scan or use alone."
        ),
    )
    args = parser.parse_args(argv)

    candidates: list[Path] = []
    seen: set[str] = set()
    for path in [*tracked_extension_paths(), *(Path(p) for p in args.path)]:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(path)

    missing = audit_notices(candidates)
    for path in missing:
        print(path)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())