#!/usr/bin/env python3
# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Lint a MicroPad configuration before it is published (A6).

Answers one question with an exit code: would the pad store and show this
configuration as written, or would it clip a value, drop an oversized payload, or
refuse the whole page? The analysis itself lives in ``micropad.lint`` (shared with
``POST /api/lint`` in the configurator), so the CLI, the website and CI cannot
disagree about a config.

    python3 scripts/lint_config.py                     # config.json (or $MICROPAD_CONFIG_PATH)
    python3 scripts/lint_config.py path/to/config.json --json
    python3 scripts/lint_config.py --strict            # warnings fail the run too

Exit codes: 0 = publishable, 1 = errors (or warnings with --strict), 2 = the file
could not be read.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from micropad.lint import analyze

DEFAULT_CONFIG_NAME = "config.json"
CONFIG_PATH_ENV = "MICROPAD_CONFIG_PATH"


def resolve_config_path(argument: str | None) -> Path:
    """Config path resolution, matching the configurator's ConfigStore precedence."""
    if argument:
        return Path(argument)
    from_env = os.environ.get(CONFIG_PATH_ENV)
    return Path(from_env) if from_env else Path(DEFAULT_CONFIG_NAME)


def format_report(document: dict, *, path: Path, strict: bool) -> tuple[str, int]:
    """Human-readable report plus the exit code for one analysed config."""
    analysis = analyze(document)
    lines = [f"config: {path}"]
    limits = analysis.limits
    lines.append(
        "budgets: catalog {catalog}/{catalog_limit} B, MQTT buffer {buffer} B, "
        "page ceiling {page} B".format(
            catalog=analysis.catalog_bytes,
            catalog_limit=limits["catalog_bytes"],
            buffer=limits["mqtt_buffer_bytes"],
            page=limits["page_bytes"],
        )
    )
    for page_id, size in sorted(analysis.page_bytes.items()):
        lines.append(f"  page {page_id}: {size} B")
    if not analysis.findings:
        lines.append("findings: none — the pad stores and shows this configuration as written")
    else:
        lines.append("findings:")
        for finding in analysis.findings:
            where = f" [{finding.where}]" if finding.where else ""
            lines.append(f"  {finding.severity}: {finding.code}: {finding.message}{where}")
    errors = [f for f in analysis.findings if f.severity == "error"]
    warnings = [f for f in analysis.findings if f.severity != "error"]
    failed = bool(errors) or (strict and bool(warnings))
    lines.append(
        "result: "
        + (
            f"NOT PUBLISHABLE ({len(errors)} error(s), {len(warnings)} warning(s))"
            if failed
            else f"publishable ({len(warnings)} warning(s))"
        )
    )
    return "\n".join(lines), (1 if failed else 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", nargs="?", help="configuration file (default: config.json)")
    parser.add_argument("--json", action="store_true", help="print the analysis as JSON")
    parser.add_argument(
        "--strict", action="store_true", help="treat 'the device changes it' warnings as failure"
    )
    args = parser.parse_args(argv)

    path = resolve_config_path(args.config)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        print(f"cannot read {path}: {error}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as error:
        print(f"{path} is not valid JSON: {error}", file=sys.stderr)
        return 2
    if not isinstance(document, dict):
        print(f"{path} must contain a JSON object", file=sys.stderr)
        return 2

    if args.json:
        analysis = analyze(document)
        print(json.dumps(analysis.as_dict(), indent=2, ensure_ascii=False))
        errors = [f for f in analysis.findings if f.severity == "error"]
        warnings = [f for f in analysis.findings if f.severity != "error"]
        return 1 if (errors or (args.strict and warnings)) else 0

    report, code = format_report(document, path=path, strict=args.strict)
    print(report)
    return code


if __name__ == "__main__":
    sys.exit(main())
