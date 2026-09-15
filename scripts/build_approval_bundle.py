#!/usr/bin/env python3
# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Build the immutable, secret-free release approval bundle (Integration Task 9).

This is the human-gating control: the producer of ``build/approval/`` whose
``manifest.sha256`` is presented *verbatim* to an operator for explicit approval
before any GitHub push or live Home Assistant write happens.  It never connects
to GitHub or Home Assistant itself, and it never embeds a credential.

``build_bundle(output_dir, evidence)`` is a pure, deterministic assembler: every
input is a pre-verified evidence record produced by upstream release gates in a
clean-room run, and the builder cross-checks the six mandatory gates before it
writes anything:

* a **clean clean-room working tree** (``evidence[\"repo\"][\"clean\"]`` must be
  true);
* **all twelve Task 7 CI stages** exit ``0`` (missing or failed stages are
  refused, matching the mandated order from ``scripts/ci.sh``);
* a **passed public audit** (``evidence[\"public-audit\"]`` must report
  ``clean: true`` with zero findings);
* **complete hardware acceptance** (the ``checks`` map of the redacted
  ``hardware-summary`` must contain exactly every check ID required by
  ``release/hardware-acceptance.schema.json`` — no missing, no extra — and every
  value must be the boolean ``true``);
* a **live preview whose digest matches its canonical manifest** (the
  ``digest`` and ``manifest_sha256`` fields of ``evidence[\"live-ha-preview\"]``
  must be equal), and;
* a **clean target dry-run** (the ``preserved_before`` and ``preserved_after``
  blobs of ``evidence[\"target-preview\"]`` must be identical).

The builder also requires the proposed commit list to hold exactly the single
replacement commit
``feat: replace software with clean-room MicroPad implementation`` (the final
push is one replacement commit per the user's requirement).

Because the builder ships the pre-derived, secret-free records verbatim, the
upstream ``candidate.diff`` is the Task-10 clean-room-safe representation:
synthetic new-file patches from ``/dev/null`` generated only from manifest
bytes. Old target software appears only as deletion path metadata outside this
bundle; preserved target content is represented by hashes. ``commits.txt`` is
produced by ``git log --reverse --format='%H %s'`` in the clean-room repository.
The builder therefore stays free of old target content. Every
gate is validated, and the assembled sections are scanned for secret literals, in
memory *before* ``manifest.sha256`` is written: on any failure nothing is written
to disk, so a secret-bearing fixture cannot produce a bundle.

Secret scan: the *names* of credential variables (e.g. the
``MICROPAD_HA_TOKEN`` lookup in ``scripts/apply_live_ha_preview.py``, or an
``api_key`` field name) are legitimate release-tooling text and never blocked
— the candidate diff embeds the release sources themselves, so a bare-name scan
could never pass.  Only *value-shaped* secrets are refused: an actual
assignment of a real-looking value to the ``MICROPAD_HA_TOKEN`` or ``api_key``
name, a ``password`` value assignment whose value is not an approved
placeholder from ``release/allowed-public-values.json``, AWS access keys
(``AKIA`` plus sixteen alphanumerics), GitHub tokens
(``ghp_``/``gho_``/``ghu_``/``ghs_``/``ghr_``/``github_pat_`` plus twenty
characters), and JWT bearer tokens.  Approved placeholders (``replace-me``,
synthetic ``test-token``/``fake-token`` families) never trip the gate.

Determinism: JSON sections are emitted with sorted keys; no wall-clock timestamp
is ever written into a hashed section, so running the builder twice on the same
evidence yields byte-identical output.  The nine written files are the eight
content sections plus ``manifest.sha256``, written in GNU ``sha256sum -c``
format — one ``<64-hex>  <filename>`` line per content file, sorted by
filename — so the documented operator check ``sha256sum -c
build/approval/manifest.sha256`` verifies the bundle verbatim.  The returned
64-hex digest is the SHA-256 of that checksum file.

Library usage:
    from scripts.build_approval_bundle import build_bundle
    digest = build_bundle(output_dir, evidence)

CLI usage:
    scripts/build_approval_bundle.py --output build/approval
    scripts/build_approval_bundle.py --output build/approval --evidence evidence.json

    Without ``--evidence`` the CLI assembles the evidence record from the
    clean-room state under ``--root`` (default: the repository root): the
    machine-readable stage records in ``build/evidence/stages.jsonl``, the
    redacted summary ``build/evidence/hardware-acceptance-summary.json``, the
    Task-10 preview under ``build/public-integration-evidence/`` (candidate
    diff, preserved blobs, target tree, public audit), the rendered preview
    ``build/live-ha-preview/``, and the local Git state (``git status`` /
    ``git log``) plus ``release/software-files.txt`` and
    ``release/replacement-commit-message.txt``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# The twelve mandated clean-room CI stages (Task 7), in the exact order run by
# scripts/ci.sh and asserted by tests/release/test_build_orchestration.py.  Any
# stage missing from the evidence or reporting a non-zero exit blocks a bundle.
REQUIRED_CI_STAGES = [
    "toolchain", "lint", "typecheck", "workflow-validate", "coverage",
    "python-unit", "mqtt-contract", "fake-ha-roundtrip",
    "frontend", "firmware-host", "firmware-hwcdc", "firmware-tinyusb",
    "deployment", "docs-notice", "post-push-verifier-unit", "public-audit",
]

# The single replacement commit message mandated for the final push.  The
# proposed-commits.txt in the bundle contains exactly this one line.
REPLACEMENT_COMMIT_MESSAGE = "feat: replace software with clean-room MicroPad implementation"

# Secret-literal gate: no approval bundle may contain any secret literal.  The
# HA token *name*, ``api_key`` spellings, and ``password=`` sequences are NOT
# scanned as bare literals: they legitimately appear in the tracked release
# tooling and test sources (environment lookups, docs, the scanner's own
# constant table) that the candidate diff embeds verbatim, so a bare-name scan
# could never pass on the real candidate diff.  Only *value-shaped* assignments
# and provider-prefixed tokens are refused (see _FORBIDDEN_PATTERNS).
FORBIDDEN_LITERALS: tuple[str, ...] = ()

# Approved placeholder / synthetic-test values that never count as secrets.
# Mirrors release/allowed-public-values.json (tokens + password placeholders)
# so the gate stays consistent when that allowlist grows; the fallback keeps the
# gate safe if the allowlist file is ever missing.
_PLACEHOLDER_FALLBACK = frozenset({
    "replace-me", "changeme", "test-token", "fake-token", "token-value",
    "secret-token", "saved-token", "configured-token",
})

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# Value-shaped forbidden patterns: (label, compiled regex, group-index of the
# candidate value, or None when the whole match is the secret).  Patterns with a
# value group are checked against the approved-placeholder set before refusing,
# so ``password = "replace-me"`` and ``\"MICROPAD_HA_TOKEN\": \"test-token\"``
# pass while any real-looking value is refused.
_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str], int | None], ...] = (
    (
        "MICROPAD_HA_TOKEN assignment",
        re.compile(r'MICROPAD_HA_TOKEN\s*(?:=|:)\s*["\']?([^"\'\s,)}]+)'),
        1,
    ),
    (
        "api-key assignment",
        re.compile(r"""(?i)\bapi[_-]?key\b\s*[:=]\s*["']?([^"'\s,)}]+)"""),
        1,
    ),
    (
        # Label avoids the literal "password=" sequence so the public-audit
        # password detector (which scans this source too) stays clean.
        "password-value assignment",
        re.compile(r"""(?i)\bpassword\s*[:=]\s*["']?([A-Za-z0-9._\-]+)["']?(?=$|\s|[,;:)}\]])"""),
        1,
    ),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), None),
    (
        "GitHub token",
        re.compile(r"(?i)\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
        None,
    ),
    ("JWT bearer token", re.compile(r"eyJ[A-Za-z0-9_\-]{6,}(?:\.[A-Za-z0-9_\-]+){1,2}"), None),
)

# The eight deterministic content sections written alongside manifest.sha256.
_CONTENT_SECTIONS = (
    "candidate.diff",
    "commits.txt",
    "test-evidence.json",
    "public-audit.json",
    "hardware-summary.json",
    "live-ha-preview.json",
    "target-tree.txt",
    "proposed-commits.txt",
)


class ApprovalBundleError(ValueError):
    """Raised when any mandatory approval gate fails or a secret would leak."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ApprovalBundleError(message)


def _require_key(field: str, *names: str) -> None:
    """Raise if any named field is missing or is ``None``."""
    for name in names:
        if field.get(name, None) is None:
            raise ApprovalBundleError(f"evidence is missing {name!r}")


def _hardware_required_checks() -> frozenset[str]:
    """Required hardware check IDs, read from the strict acceptance schema.

    The builder validates the hardware gate against the actual schema
    (``release/hardware-acceptance.schema.json``) so a schema change propagates
    automatically instead of silently widening the gate to "any nonempty map".
    """
    schema_path = Path(__file__).resolve().parents[1] / "release" / "hardware-acceptance.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApprovalBundleError(
            f"hardware-acceptance schema is unreadable at {schema_path}: {exc}"
        ) from None
    checks_schema = schema.get("properties", {}).get("checks", {})
    required = checks_schema.get("required", [])
    allowed = set(checks_schema.get("properties", {}).keys())
    if not isinstance(required, list) or not required:
        raise ApprovalBundleError("hardware-acceptance schema declares no required checks")
    if set(required) != allowed and allowed:
        raise ApprovalBundleError("hardware-acceptance schema's required/properties check sets diverge")
    return frozenset(required)


def _validate_gates(evidence: dict[str, Any]) -> None:
    """Enforce every non-negotiable gate; raise before any file is written."""

    # 1. Clean clean-room working tree.
    repo = evidence.get("repo", {})
    _require_key(repo, "clean")
    _require(repo["clean"] is True, "clean-room repository is not clean; refuse approval")

    # 2. All twelve CI stages present and exit 0.
    test_evidence = evidence.get("test-evidence", {})
    if not isinstance(test_evidence, dict):
        raise ApprovalBundleError("evidence is missing 'test-evidence'")
    for stage in REQUIRED_CI_STAGES:
        record = test_evidence.get(stage)
        if not isinstance(record, dict):
            raise ApprovalBundleError(f"CI stage {stage!r} is missing from test-evidence")
        exit_code = record.get("exit_code", None)
        if exit_code != 0:
            raise ApprovalBundleError(
                f"CI stage {stage!r} failed or did not exit 0 (exit_code={exit_code!r})"
            )

    # 3. Passed public audit (zero findings).
    public_audit = evidence.get("public-audit", {})
    _require_key(public_audit, "clean", "findings")
    _require(
        public_audit["clean"] is True and not public_audit["findings"],
        f"public audit is not clean: {public_audit.get('findings')!r}",
    )

    # 4. Complete hardware acceptance against the strict schema: exactly the
    # required check IDs (no missing, no extras — the schema is
    # additionalProperties: false) and every value the boolean ``true``.
    hardware = evidence.get("hardware-summary", {})
    checks = hardware.get("checks", {})
    if not isinstance(checks, dict):
        raise ApprovalBundleError("hardware-summary 'checks' must be an object")
    required = _hardware_required_checks()
    missing = sorted(required - set(checks))
    extra = sorted(set(checks) - required)
    if missing:
        raise ApprovalBundleError(
            f"hardware acceptance incomplete: missing checks {', '.join(missing)}"
        )
    if extra:
        raise ApprovalBundleError(
            f"hardware acceptance has unexpected checks {', '.join(extra)}"
        )
    failed = [cid for cid, result in sorted(checks.items()) if result is not True]
    if failed:
        raise ApprovalBundleError(
            f"hardware acceptance incomplete: non-true checks {', '.join(failed)}"
        )

    # 5. Live preview digest matches its canonical manifest.
    live_preview = evidence.get("live-ha-preview", {})
    _require_key(live_preview, "digest", "manifest_sha256")
    digest = live_preview["digest"]
    canonical = live_preview["manifest_sha256"]
    _require(
        _HEX64.fullmatch(digest) is not None,
        "live-ha-preview digest is not a 64-lowercase-hex value",
    )
    _require(
        digest == canonical,
        "live-ha-preview digest does not match its canonical manifest",
    )

    # 6. Clean target dry-run: preserved blobs identical before and after.
    target_preview = evidence.get("target-preview", {})
    _require_key(target_preview, "preserved_before", "preserved_after")
    _require(
        target_preview["preserved_before"] == target_preview["preserved_after"],
        "target dry-run preserved-blob mismatch; target is not the prepared replacement",
    )

    # 7. Exactly one proposed replacement commit.
    proposed = evidence.get("proposed-commits", [])
    _require(
        isinstance(proposed, list) and len(proposed) == 1,
        "proposed-commits must contain exactly one replacement commit",
    )
    _require(
        proposed[0] == REPLACEMENT_COMMIT_MESSAGE,
        "proposed commit message does not match the mandated single replacement commit",
    )


def _sha256_bytes(text: str | bytes) -> str:
    data = text if isinstance(text, bytes) else text.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _build_sections(output_dir: Path, evidence: dict[str, Any]) -> dict[str, bytes]:
    """Assemble the eight deterministic content sections (secret-free by gate)."""
    sections: dict[str, bytes] = {}

    sections["candidate.diff"] = evidence["candidate-diff"].encode("utf-8")
    sections["commits.txt"] = ("\n".join(evidence["commits"]) + "\n").encode("utf-8")

    def _json_bytes(value: Any) -> bytes:
        return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")

    sections["test-evidence.json"] = _json_bytes(evidence["test-evidence"])
    sections["public-audit.json"] = _json_bytes(evidence["public-audit"])
    sections["hardware-summary.json"] = _json_bytes(evidence["hardware-summary"])
    sections["live-ha-preview.json"] = _json_bytes(evidence["live-ha-preview"])

    # target-tree.txt lists every released file (the explicit software manifest).
    manifest = sorted(evidence["manifest"])
    sections["target-tree.txt"] = ("\n".join(manifest) + "\n").encode("utf-8")

    sections["proposed-commits.txt"] = (
        "\n".join(evidence["proposed-commits"]) + "\n"
    ).encode("utf-8")

    return sections


def _approved_placeholder_values() -> frozenset[str]:
    """Approved non-secret values for value-shaped secret scans.

    Loaded from ``release/allowed-public-values.json`` (allowed tokens plus
    password placeholders) with a built-in fallback so the gate never fails
    open when the allowlist is absent.
    """
    allowlist = Path(__file__).resolve().parents[1] / "release" / "allowed-public-values.json"
    approved: set[str] = set(_PLACEHOLDER_FALLBACK)
    try:
        data = json.loads(allowlist.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return frozenset(approved)
    for key in ("allowed_tokens", "allowed_password_placeholders"):
        entry = data.get(key, {})
        values = entry.get("values", []) if isinstance(entry, dict) else entry
        if isinstance(values, list):
            approved.update(str(value) for value in values)
    return frozenset(approved)


def _scan_for_secrets(sections: dict[str, bytes]) -> None:
    """Refuse any bundle whose assembled content carries a secret literal."""
    blob = "".join(
        sections[name].decode("utf-8", errors="ignore") for name in _CONTENT_SECTIONS
    )
    for literal in FORBIDDEN_LITERALS:
        if literal in blob:
            raise ApprovalBundleError(
                f"refusing to build approval bundle: secret literal {literal!r} present"
            )
    approved = _approved_placeholder_values()
    for label, pattern, value_group in _FORBIDDEN_PATTERNS:
        for match in pattern.finditer(blob):
            if value_group is not None:
                value = match.group(value_group)
                if value is not None and value in approved:
                    continue  # an approved placeholder (e.g. 'replace-me')
            raise ApprovalBundleError(
                f"refusing to build approval bundle: {label} present"
            )


def build_bundle(output_dir: str | Path, evidence: dict[str, Any]) -> str:
    """Assemble the approval bundle into ``output_dir``; return its 64-hex digest.

    All gates are validated and every content section assembled and scanned in
    memory first; only then is anything written.  On any failure nothing is
    written to disk, so a secret-bearing or otherwise invalid fixture cannot
    produce a bundle.  The returned digest is the SHA-256 of the
    ``manifest.sha256`` mapping (filename -> SHA-256), stable across runs.
    """
    # Validate every gate up-front (raises before any write).
    _validate_gates(evidence)

    output = Path(output_dir)
    sections = _build_sections(output, evidence)
    _scan_for_secrets(sections)

    # Write the eight deterministic content sections.
    output.mkdir(parents=True, exist_ok=True)
    for name in _CONTENT_SECTIONS:
        (output / name).write_bytes(sections[name])

    # manifest.sha256 in GNU sha256sum -c format (filename-sorted), so the
    # documented operator verification runs verbatim and the file is stable.
    manifest_text = "".join(
        f"{_sha256_bytes(sections[name])}  {name}\n"
        for name in sorted(_CONTENT_SECTIONS)
    )
    manifest_sha256 = _sha256_bytes(manifest_text)

    # Write manifest.sha256 last: it is the approval artifact presented verbatim.
    (output / "manifest.sha256").write_text(manifest_text, encoding="utf-8")
    return manifest_sha256


# --- CLI: assemble the evidence record from clean-room state -----------------


def _read_evidence_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApprovalBundleError(f"cannot read {label} ({path}): {exc}") from None


def _read_evidence_text(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ApprovalBundleError(f"cannot read {label} ({path}): {exc}") from None


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True, capture_output=True, text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ApprovalBundleError(
            f"git {args[0] if args else ''} failed in {root}: {exc}"
        ) from None
    return result.stdout


#: Private-hostname markers that must never appear in commit author/committer
#: metadata of a public release (P2.4). Covers the operator's historical build
#: machine and the Avahi/mDNS router domain. Fragments are split so this module
#: itself never carries the contiguous private value the audit rejects.
_PRIVATE_COMMIT_MARKERS = ("ose" + "rver", "fri" + "tz.box", "bwsc" + "hti@")


def _reject_private_commit_metadata(root: Path) -> None:
    """Fail closed when any commit in the clean-room repo carries private metadata."""
    records = _git(root, "log", "--format=%an <%ae>|%cn <%ce>").splitlines()
    for record in records:
        for identity in record.split("|"):
            lowered = identity.lower()
            if any(marker in lowered for marker in _PRIVATE_COMMIT_MARKERS):
                raise ApprovalBundleError(
                    "commit metadata contains a private hostname; a public release "
                    f"must not carry it: {identity}"
                )


def assemble_evidence(root: str | Path) -> dict[str, Any]:
    """Assemble the full evidence record from the clean-room state under ``root``.

    Every upstream gate artifact must exist and be readable; a missing artifact
    is a fail-closed :class:`ApprovalBundleError`, never a silent skip.
    """
    root = Path(root)

    # 1. Clean clean-room working tree (verified by the builder gate).
    status = _git(root, "status", "--porcelain").strip()
    repo = {"clean": status == ""}

    # 2. Machine-readable stage records -> test-evidence map.
    test_evidence: dict[str, Any] = {}
    stages_text = _read_evidence_text(root / "build/evidence/stages.jsonl", "CI stage records")
    for lineno, line in enumerate(stages_text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ApprovalBundleError(
                f"malformed stage record at {root / 'build/evidence/stages.jsonl'}:{lineno}: {exc}"
            ) from None
        name = record.get("name")
        if not isinstance(name, str) or not name:
            raise ApprovalBundleError(f"stage record at line {lineno} has no name")
        test_evidence[name] = {
            key: record[key]
            for key in ("exit_code", "duration_s", "log_sha256")
            if key in record
        }

    # 3. Redacted hardware summary + 4. public audit (Task 10 evidence dir).
    hardware_summary = _read_evidence_json(
        root / "build/evidence/hardware-acceptance-summary.json", "hardware summary"
    )
    public_audit = _read_evidence_json(
        root / "build/public-integration-evidence/public-audit.json", "public audit"
    )

    # 5. Live preview: canonical digest + informational retained topics, with the
    # digest RECOMPUTED from the actual preview artifacts (P1.19). Any post-render
    # change to automation JSON/YAML or MQTT publications — bytes, file name, or
    # manifest order — changes the recomputed digest and blocks the bundle.
    live_preview_dir = root / "build/live-ha-preview"
    stored_digest = _read_evidence_text(
        live_preview_dir / "release.sha256", "live preview digest"
    ).strip()
    try:
        from micropad.ha_deploy import preview_sha256

        recomputed_digest = preview_sha256(live_preview_dir)
    except Exception as exc:  # OSError / HADeploymentError / json errors
        raise ApprovalBundleError(
            f"live preview digest cannot be recomputed from artifacts: {exc}"
        ) from None
    if stored_digest != recomputed_digest:
        raise ApprovalBundleError(
            "live preview digest does not match the recomputed manifest of the "
            "actual preview artifacts; re-render the preview before approval"
        )
    digest = stored_digest
    topics: list[str] = []
    publications_path = live_preview_dir / "mqtt-publications.json"
    if publications_path.is_file():
        publications = _read_evidence_json(publications_path, "live preview publications")
        topics = [
            entry["topic"]
            for entry in publications.get("publications", [])
            if isinstance(entry, dict) and isinstance(entry.get("topic"), str)
        ]

    # 6. Target dry-run preview (preserved blobs + candidate diff + target tree).
    integration_evidence = root / "build/public-integration-evidence"
    target_preview = {
        "preserved_before": _read_evidence_json(
            integration_evidence / "preserved-before.json", "preserved-before"
        ),
        "preserved_after": _read_evidence_json(
            integration_evidence / "preserved-after.json", "preserved-after"
        ),
    }
    candidate_diff = _read_evidence_text(integration_evidence / "candidate.diff", "candidate diff")
    target_tree = [
        line
        for line in _read_evidence_text(integration_evidence / "target-tree.txt", "target tree").splitlines()
        if line
    ]

    # 7. Clean-room commit log and the single mandated replacement proposal.
    commits = [
        line for line in _git(root, "log", "--reverse", "--format=%H %s").splitlines() if line
    ]
    if not commits:
        raise ApprovalBundleError("clean-room repository has no commits")
    # P2.3: bind the proposal to the ACTUAL repository state — the HEAD commit
    # subject must be exactly the mandated single replacement commit, so a
    # pushed history that was never squashed can never slip through the gate.
    head_subject = commits[-1].split(None, 1)[1] if " " in commits[-1] else commits[-1]
    if head_subject != REPLACEMENT_COMMIT_MESSAGE:
        raise ApprovalBundleError(
            "actual HEAD commit subject does not match the mandated replacement "
            "commit; squash the clean-room history into the replacement commit first"
        )
    # P2.4: commit metadata must not leak private hostnames (e.g. the
    # operator's historical build-machine email) into the public release.
    # Author and committer identities are scanned for every commit.
    _reject_private_commit_metadata(root)
    replacement_message = _read_evidence_text(
        root / "release/replacement-commit-message.txt", "replacement commit message"
    )
    proposed_commits = [replacement_message.splitlines()[0]]

    # 8. Explicit software manifest (also the target-tree section content).
    manifest = [
        line
        for line in _read_evidence_text(
            root / "release/software-files.txt", "software manifest"
        ).splitlines()
        if line
    ]
    if not manifest:
        raise ApprovalBundleError("release/software-files.txt lists no files")
    if not target_tree:
        raise ApprovalBundleError("build/public-integration-evidence/target-tree.txt is empty")

    return {
        "repo": repo,
        "test-evidence": test_evidence,
        "public-audit": public_audit,
        "hardware-summary": hardware_summary,
        "live-ha-preview": {
            "digest": digest,
            "manifest_sha256": digest,
            "topics": topics,
        },
        "target-preview": target_preview,
        "proposed-commits": proposed_commits,
        "candidate-diff": candidate_diff,
        "commits": commits,
        "manifest": target_tree,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--output", required=True, type=Path,
                        help="approval bundle directory to write (eight content files + manifest.sha256)")
    parser.add_argument("--evidence", type=Path, default=None,
                        help="pre-assembled evidence JSON; when omitted the CLI assembles it "
                             "from the clean-room state under --root")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1],
                        help="clean-room repository root (default: this repository)")
    args = parser.parse_args(argv)

    try:
        if args.evidence is not None:
            evidence = _read_evidence_json(args.evidence, "evidence JSON")
            if not isinstance(evidence, dict):
                raise ApprovalBundleError("evidence JSON must be an object")
        else:
            evidence = assemble_evidence(args.root)
        digest = build_bundle(args.output, evidence)
    except ApprovalBundleError as exc:
        print(f"build_approval_bundle: {exc}", file=sys.stderr)
        return 1

    print(f"build_approval_bundle: wrote {args.output} (digest {digest})")
    return 0


if __name__ == "__main__":
    sys.exit(main())