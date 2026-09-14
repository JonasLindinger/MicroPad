# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.


"""Immutable, secret-free approval-bundle builder (Integration Task 9).

Proves the builder (a) refuses to produce any bundle when a mandatory gate
fails or is missing, (b) raises *before* writing ``manifest.sha256`` for a
secret-bearing or otherwise invalid evidence bundle, (c) emits exactly the
nine mandated public sections with no secret literal anywhere, (d) gates
hardware acceptance against the strict JSON Schema's exact check set (no
missing, no extra, no non-boolean value), and (e) runs as a real CLI both from
a pre-assembled evidence JSON and by assembling evidence from clean-room state.

Forbidden canaries are assembled from adjacent string fragments at test runtime
(the same discipline as tests/release/test_public_audit.py) so the committed
test source never contains a contiguous secret-shaped literal — otherwise the
real bundle's ``candidate.diff`` (which embeds this test file verbatim) could
never pass the very gate it tests.
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts.build_approval_bundle import build_bundle
from scripts.build_approval_bundle import main as bundle_cli

PUBLIC_SECTIONS = {
    "candidate.diff", "commits.txt", "test-evidence.json", "public-audit.json",
    "hardware-summary.json", "live-ha-preview.json", "target-tree.txt",
    "proposed-commits.txt", "manifest.sha256",
}


def test_bundle_refuses_missing_or_failed_gate(tmp_path, complete_evidence):
    complete_evidence["test-evidence"]["firmware-tinyusb"]["exit_code"] = 1
    with pytest.raises(ValueError, match="firmware-tinyusb"):
        build_bundle(tmp_path / "approval", complete_evidence)


def test_bundle_has_exact_public_sections(tmp_path, complete_evidence):
    output = tmp_path / "approval"
    digest = build_bundle(output, complete_evidence)
    assert len(digest) == 64
    assert {p.name for p in output.iterdir()} == PUBLIC_SECTIONS
    blob = "".join(p.read_text(errors="ignore") for p in output.iterdir())
    assert "MICROPAD_HA_TOKEN" not in blob
    candidate = (output / "candidate.diff").read_text(encoding="utf-8")
    assert "--- /dev/null" in candidate
    assert "--- a/" not in candidate


def test_bundle_refuses_dirty_cleanroom(tmp_path, complete_evidence):
    bad = copy.deepcopy(complete_evidence)
    bad["repo"]["clean"] = False
    with pytest.raises(ValueError, match="repository"):
        build_bundle(tmp_path / "approval", bad)


def test_bundle_refuses_failed_public_audit(tmp_path, complete_evidence):
    bad = copy.deepcopy(complete_evidence)
    bad["public-audit"]["clean"] = False
    bad["public-audit"]["findings"] = {"token": 1}
    with pytest.raises(ValueError, match="audit"):
        build_bundle(tmp_path / "approval", bad)


def test_bundle_refuses_incomplete_hardware(tmp_path, complete_evidence):
    bad = copy.deepcopy(complete_evidence)
    bad["hardware-summary"]["checks"]["wake-action-immediate"] = False
    with pytest.raises(ValueError, match="hardware"):
        build_bundle(tmp_path / "approval", bad)


def test_bundle_refuses_live_preview_hash_mismatch(tmp_path, complete_evidence):
    bad = copy.deepcopy(complete_evidence)
    bad["live-ha-preview"]["manifest_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="preview"):
        build_bundle(tmp_path / "approval", bad)


def test_bundle_refuses_preserved_blob_mismatch(tmp_path, complete_evidence):
    bad = copy.deepcopy(complete_evidence)
    bad["target-preview"]["preserved_after"] = {"blob": "different"}
    with pytest.raises(ValueError, match="preserved"):
        build_bundle(tmp_path / "approval", bad)


def test_bundle_refuses_multiple_proposed_commits(tmp_path, complete_evidence):
    bad = copy.deepcopy(complete_evidence)
    bad["proposed-commits"].append("chore: second commit")
    with pytest.raises(ValueError, match="proposed"):
        build_bundle(tmp_path / "approval", bad)


# --- hardware gate validates the strict acceptance schema, not any map --------


def test_hardware_gate_requires_every_schema_check_present(tmp_path, complete_evidence):
    # Deliberate upgrade of the weak "any nonempty all-true map" gate: deleting a
    # required check ID must refuse, even when the remaining map is all-true.
    bad = copy.deepcopy(complete_evidence)
    del bad["hardware-summary"]["checks"]["matrix-all-twelve"]
    with pytest.raises(ValueError, match="matrix-all-twelve"):
        build_bundle(tmp_path / "approval", bad)


def test_hardware_gate_rejects_unknown_extra_checks(tmp_path, complete_evidence):
    # The schema is additionalProperties: false, so a fabricated check ID must
    # refuse even though every value is true.
    bad = copy.deepcopy(complete_evidence)
    bad["hardware-summary"]["checks"]["bench-extra-check"] = True
    with pytest.raises(ValueError, match="bench-extra-check"):
        build_bundle(tmp_path / "approval", bad)


@pytest.mark.parametrize("non_boolean", [1, "true", "pass", 0])
def test_hardware_gate_requires_boolean_true_values(tmp_path, complete_evidence, non_boolean):
    # All values must be the boolean True — a truthy non-boolean (int/string)
    # is not an accepted physical check result.
    bad = copy.deepcopy(complete_evidence)
    bad["hardware-summary"]["checks"]["wake-action-immediate"] = non_boolean
    with pytest.raises(ValueError, match="wake-action-immediate"):
        build_bundle(tmp_path / "approval", bad)


# --- value-shaped secret scan (names are allowed, real values are not) --------


def test_secret_bearing_fixture_produces_no_bundle(tmp_path, complete_evidence):
    # Canary built from fragments so the committed test source itself stays
    # secret-free (it is embedded in the real bundle's candidate.diff).  The
    # in-memory candidate-diff therefore carries a real-looking HA token value.
    bad = copy.deepcopy(complete_evidence)
    bad["candidate-diff"] += (
        "\n+MICROPAD_HA_TOKEN = '" + "eyJ" + "hbGciOiJIUzI1NiJ9." + "a" * 24 + "'\n"
    )
    output = tmp_path / "approval"
    with pytest.raises(ValueError, match="MICROPAD_HA_TOKEN"):
        build_bundle(output, bad)
    # No bundle file may exist; a secret-bearing fixture must not leave output.
    assert not output.exists() or list(output.iterdir()) == []


def test_secret_scan_allows_token_name_in_env_lookup(tmp_path, complete_evidence):
    # The HA token *name* legitimately appears in release tooling (environment
    # lookups, docs, tests).  A bare name must never block the real bundle.
    good = copy.deepcopy(complete_evidence)
    good["candidate-diff"] += '\n+token = os.environ.get("MICROPAD_HA_TOKEN", "")\n'
    build_bundle(tmp_path / "approval", good)


def test_secret_scan_allows_synthetic_token_value(tmp_path, complete_evidence):
    # The approved synthetic test token (release/allowed-public-values.json) is
    # allowed even in a MICROPAD_HA_TOKEN value position.
    good = copy.deepcopy(complete_evidence)
    good["candidate-diff"] += '\nenv = {"MICROPAD_HA_TOKEN": "test-token"}\n'
    build_bundle(tmp_path / "approval", good)


def test_secret_scan_refuses_password_with_real_value(tmp_path, complete_evidence):
    bad = copy.deepcopy(complete_evidence)
    bad["candidate-diff"] += '\n+password = "hunter2"\n'
    with pytest.raises(ValueError, match="password"):
        build_bundle(tmp_path / "approval", bad)


def test_secret_scan_allows_password_placeholder(tmp_path, complete_evidence):
    # The documented unfilled placeholder must never be read as a secret.
    good = copy.deepcopy(complete_evidence)
    good["candidate-diff"] += "\n+password = 'replace-me'  # placeholder\n"
    build_bundle(tmp_path / "approval", good)


def test_secret_scan_refuses_api_key_literal(tmp_path, complete_evidence):
    # The three api-key spellings are refused only when assigned a value, never
    # as a bare mention (the scanner's own sources legitimately name them).
    for spelling in ("api_key", "api-key", "apikey"):
        bad = copy.deepcopy(complete_evidence)
        bad["candidate-diff"] += f"\n+{spelling} = 'sk-live-value'\n"
        with pytest.raises(ValueError, match="api"):
            build_bundle(tmp_path / "approval", bad)


def test_secret_scan_allows_api_key_bare_mention(tmp_path, complete_evidence):
    # A bare field-name mention (config schema, docs, the scanner's own
    # constant table) must never trip the gate.
    good = copy.deepcopy(complete_evidence)
    good["candidate-diff"] += '\n+// schema field: "api_key" (documentation only)\n'
    build_bundle(tmp_path / "approval", good)


def test_secret_scan_refuses_aws_access_key(tmp_path, complete_evidence):
    bad = copy.deepcopy(complete_evidence)
    bad["candidate-diff"] += "\n+aws_access = AKIA" + "1234567890ABCDEF\n"
    with pytest.raises(ValueError, match="AWS"):
        build_bundle(tmp_path / "approval", bad)


@pytest.mark.parametrize("prefix", ["ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_"])
def test_secret_scan_refuses_github_tokens(tmp_path, complete_evidence, prefix):
    bad = copy.deepcopy(complete_evidence)
    bad["candidate-diff"] += "\n+git_token = " + prefix + "x" * 36 + "\n"
    with pytest.raises(ValueError, match="GitHub"):
        build_bundle(tmp_path / "approval", bad)


def test_secret_scan_refuses_jwt_bearer_token(tmp_path, complete_evidence):
    bad = copy.deepcopy(complete_evidence)
    bad["candidate-diff"] += "\n+token = '" + "eyJ" + "hbGciOiJIUzI1NiJ9." + "a" * 24 + "'\n"
    with pytest.raises(ValueError, match="JWT"):
        build_bundle(tmp_path / "approval", bad)


def test_bundle_build_is_byte_deterministic(tmp_path, complete_evidence):
    # Same evidence in -> byte-identical bundle out (sorted keys, no timestamps).
    first = tmp_path / "first"
    second = tmp_path / "second"
    digest_a = build_bundle(first, complete_evidence)
    digest_b = build_bundle(second, complete_evidence)
    assert digest_a == digest_b
    assert sorted(p.name for p in first.iterdir()) == sorted(p.name for p in second.iterdir())
    for name in PUBLIC_SECTIONS:
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_manifest_hashes_every_section_by_filename(tmp_path, complete_evidence):
    output = tmp_path / "approval"
    build_bundle(output, complete_evidence)
    manifest_text = (output / "manifest.sha256").read_text(encoding="utf-8")
    entries = {}
    for line in manifest_text.splitlines():
        digest, _, name = line.partition("  ")
        entries[name] = digest
    assert set(entries) == PUBLIC_SECTIONS - {"manifest.sha256"}
    for name, digest in entries.items():
        assert digest == hashlib.sha256((output / name).read_bytes()).hexdigest()
        assert len(digest) == 64
    # Sorted by filename so the checksum file is deterministic across runs.
    assert [line.split("  ")[1] for line in manifest_text.splitlines()] == sorted(entries)


def test_documented_sha256sum_check_passes(tmp_path, complete_evidence):
    """The documented operator verification runs verbatim on the bundle."""
    output = tmp_path / "approval"
    build_bundle(output, complete_evidence)
    result = subprocess.run(
        ["sha256sum", "-c", "manifest.sha256"],
        cwd=output, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count(": OK") == len(PUBLIC_SECTIONS) - 1


# --- CLI: --evidence JSON and clean-room state assembly ------------------------


def test_cli_builds_from_evidence_json(tmp_path, complete_evidence):
    evidence_file = tmp_path / "evidence.json"
    evidence_file.write_text(
        json.dumps(complete_evidence, indent=2, sort_keys=True), encoding="utf-8"
    )
    output = tmp_path / "approval"
    assert bundle_cli(["--output", str(output), "--evidence", str(evidence_file)]) == 0
    assert {p.name for p in output.iterdir()} == PUBLIC_SECTIONS


def test_cli_requires_output_argument():
    with pytest.raises(SystemExit):
        bundle_cli([])


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def _render_preview(root: Path) -> str:
    """Render a deterministic live-HA preview inside the synthetic root.

    Uses the real renderer so the digest is computed over the ACTUAL preview
    artifacts and the P1.19 recompute gate is exercised honestly.
    """
    from micropad.models import default_config
    from scripts.render_live_ha_preview import main as render_main

    config_path = root / "config.json"
    config_path.write_text(
        default_config().model_dump_json(by_alias=True), encoding="utf-8"
    )
    preview_dir = root / "build" / "live-ha-preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    assert render_main(["--config", str(config_path), "--output", str(preview_dir)]) == 0
    return (preview_dir / "release.sha256").read_text(encoding="utf-8").strip()


def _synthetic_root(tmp_path: Path) -> Path:
    """A self-contained clean-room state the CLI can assemble evidence from.

    Everything is synthetic and local: a one-commit Git repository, a
    two-file software manifest, complete 12-stage stage records, a redacted
    hardware summary over the schema's 21 required checks, a Task-10-style
    integration evidence directory, and a rendered-preview digest.  No network,
    no real person, no credential.
    """
    from scripts.validate_hardware_acceptance import redact_summary
    from tests.release.conftest import CI_STAGES, FIRMWARE_SHA256, HARDWARE_CHECK_IDS

    root = tmp_path / "root"
    (root / "release").mkdir(parents=True)
    (root / "firmware").mkdir()
    (root / "build" / "evidence").mkdir(parents=True)
    (root / "build" / "public-integration-evidence").mkdir(parents=True)
    (root / "build" / "live-ha-preview").mkdir(parents=True)

    (root / "release" / "software-files.txt").write_text(
        "README.md\nfirmware/main.ino\n", encoding="utf-8"
    )
    (root / "release" / "replacement-commit-message.txt").write_text(
        "feat: replace software with clean-room MicroPad implementation\n\n"
        "Replace firmware, Home Assistant automation, configurator, tests,\n"
        "deployment assets, README, and software documentation with the reviewed\n"
        "clean-room implementation.\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# Synthetic clean room\n", encoding="utf-8")
    (root / "firmware" / "main.ino").write_text("// synthetic clean-room firmware\n", encoding="utf-8")

    stages_lines = []
    for stage in CI_STAGES:
        stages_lines.append(
            json.dumps(
                {
                    "name": stage,
                    "exit_code": 0,
                    "duration_s": 0.0,
                    "log_sha256": hashlib.sha256(stage.encode("utf-8")).hexdigest(),
                }
            )
        )
    (root / "build" / "evidence" / "stages.jsonl").write_text(
        "\n".join(stages_lines) + "\n", encoding="utf-8"
    )

    report = {
        "schema_version": 1,
        "board_id": "bench-01",
        "firmware_sha256": FIRMWARE_SHA256,
        "started_at": "2026-09-13T06:00:00Z",
        "completed_at": "2026-09-13T06:05:00Z",
        "checks": {cid: True for cid in HARDWARE_CHECK_IDS},
        "evidence": [
            {
                "check_id": cid,
                "kind": "manual-observation",
                "sha256": hashlib.sha256(cid.encode("utf-8")).hexdigest(),
                "captured_at": "2026-09-13T06:01:00Z",
            }
            for cid in HARDWARE_CHECK_IDS
        ],
    }
    (root / "build" / "evidence" / "hardware-acceptance-summary.json").write_text(
        json.dumps(redact_summary(report), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    preserved = {"PCB/board.kicad_pcb": "5" * 40, "LICENSE": "4" * 40}
    for name in ("preserved-before.json", "preserved-after.json"):
        (root / "build" / "public-integration-evidence" / name).write_text(
            json.dumps(preserved, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    (root / "build" / "public-integration-evidence" / "candidate.diff").write_text(
        "diff --git a/firmware/main.ino b/firmware/main.ino\n"
        "new file mode 100644\n"
        "index 0000000..aaaaaaa\n"
        "--- /dev/null\n"
        "+++ b/firmware/main.ino\n"
        "@@ -0,0 +1,1 @@\n"
        "+// synthetic clean-room firmware\n",
        encoding="utf-8",
    )
    target_tree = "LICENSE\nPCB/board.kicad_pcb\nREADME.md\nfirmware/main.ino\n"
    (root / "build" / "public-integration-evidence" / "target-tree.txt").write_text(
        target_tree, encoding="utf-8"
    )
    (root / "build" / "public-integration-evidence" / "public-audit.json").write_text(
        json.dumps({"clean": True, "files_scanned": 2, "findings": {}}) + "\n",
        encoding="utf-8",
    )
    (root / "build" / "live-ha-preview" / "release.sha256").write_text(
        _render_preview(root) + "\n", encoding="utf-8"
    )
    # Commit only after every artifact exists, so the synthetic clean-room tree
    # (build/ evidence included) has a clean working tree when the CLI gates on it.
    _git(root, "init", "--initial-branch", "main")
    _git(root, "config", "user.name", "Clean Room")
    _git(root, "config", "user.email", "cleanroom@example.test")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "seed synthetic clean-room state")
    return root


def test_cli_assembles_evidence_from_cleanroom_state(tmp_path):
    """The documented release command (--output only) must actually run."""
    root = _synthetic_root(tmp_path)
    output = tmp_path / "approval"

    assert bundle_cli(["--output", str(output), "--root", str(root)]) == 0
    assert {p.name for p in output.iterdir()} == PUBLIC_SECTIONS
    assert (output / "proposed-commits.txt").read_text(encoding="utf-8").splitlines() == [
        "feat: replace software with clean-room MicroPad implementation"
    ]
    assert (output / "target-tree.txt").read_text(encoding="utf-8") == (
        "LICENSE\nPCB/board.kicad_pcb\nREADME.md\nfirmware/main.ino\n"
    )
    commits = (output / "commits.txt").read_text(encoding="utf-8").splitlines()
    assert len(commits) == 1 and commits[0].endswith("seed synthetic clean-room state")


def test_cli_fails_closed_when_evidence_is_missing(tmp_path):
    root = _synthetic_root(tmp_path)
    (root / "build" / "live-ha-preview" / "release.sha256").unlink()
    assert bundle_cli(["--output", str(tmp_path / "approval"), "--root", str(root)]) == 1
    assert not (tmp_path / "approval").exists()


def test_evidence_rejects_post_render_preview_tampering(tmp_path):
    """P1.19: changing automation JSON/YAML or MQTT publications after rendering
    must block the approval, because the digest is recomputed from the actual
    artifacts rather than trusting the stored value."""
    from scripts.build_approval_bundle import ApprovalBundleError, assemble_evidence

    root = _synthetic_root(tmp_path)
    artifact = root / "build" / "live-ha-preview" / "automation.json"
    artifact.write_text(artifact.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "tamper preview after render")

    with pytest.raises(ApprovalBundleError, match="preview"):
        assemble_evidence(root)


def test_evidence_rejects_preview_missing_a_content_file(tmp_path):
    """Deleting one preview artifact is a fail-closed recompute error (P1.19)."""
    from scripts.build_approval_bundle import ApprovalBundleError, assemble_evidence

    root = _synthetic_root(tmp_path)
    (root / "build" / "live-ha-preview" / "mqtt-publications.json").unlink()
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "drop publication artifact")

    with pytest.raises(ApprovalBundleError, match="preview"):
        assemble_evidence(root)