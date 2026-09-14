# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Shared fixtures for the hardware acceptance release gate.

``valid_hardware_report`` is a fully synthetic, complete acceptance record: every
one of the 22 required physical check IDs is ``true``, the board is the anonymous
``bench-01``, timestamps are fixed UTC strings, and each check has exactly one
allowed evidence entry.  No real person, network device, entity ID, firmware
digest, or credential value appears — the digests are fixed lab patterns and the
board ID is a neutral bench identifier.
"""

from __future__ import annotations

import copy
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts.validate_hardware_acceptance import redact_summary

HARDWARE_CHECK_IDS = [
    "boot-full-refresh",
    "display-four-rows",
    "full-panel-partial-refresh",
    "ghosting-bound",
    "matrix-all-twelve",
    "encoder-clockwise-down",
    "encoder-counterclockwise-up",
    "encoder-push-r0c3",
    "input-during-refresh",
    "portal-random-password",
    "portal-never-sleeps",
    "portal-input-responsive",
    "portal-back-no-save",
    "wifi-mqtt-nonblocking",
    "mqtt-reconnect-resubscribe",
    "authoritative-resync",
    "usb-host-icon-after-1500ms",
    "usb-host-prevents-sleep",
    "usb-disconnect-clears-after-1500ms",
    "battery-idle-sleeps-near-60s",
    "wake-action-immediate",
    "charge-only-follows-battery-sleep",
]

# Deterministic 64-lowercase-hex lab digests (not a real build, device, or file).
FIRMWARE_SHA256 = "a1b2c3d4" * 8
EVIDENCE_KINDS = ("photo", "video", "serial-disabled", "timing-log", "manual-observation")


def _evidence_sha(index: int) -> str:
    """Deterministic 64-lowercase-hex evidence digest for a check index."""
    return f"{index:02x}" * 32


@pytest.fixture
def valid_hardware_report() -> dict:
    """A complete, strictly-schema-valid hardware acceptance record (all checks pass)."""
    return {
        "schema_version": 1,
        "board_id": "bench-01",
        "firmware_sha256": FIRMWARE_SHA256,
        "started_at": "2026-09-13T06:00:00Z",
        "completed_at": "2026-09-13T06:05:00Z",
        "checks": {cid: True for cid in HARDWARE_CHECK_IDS},
        "evidence": [
            {
                "check_id": cid,
                "kind": EVIDENCE_KINDS[index % len(EVIDENCE_KINDS)],
                "sha256": _evidence_sha(index),
                "captured_at": "2026-09-13T06:01:00Z",
            }
            for index, cid in enumerate(HARDWARE_CHECK_IDS)
        ],
    }


# The twelve mandated clean-room CI stages (Task 7), in the order ci.sh runs
# them.  Kept in lockstep with scripts/ci.sh and REQUIRED_CI_STAGES in
# scripts/build_approval_bundle.py (asserted by
# tests/release/test_build_orchestration.py).
CI_STAGES = [
    "toolchain", "python-unit", "mqtt-contract", "fake-ha-roundtrip",
    "frontend", "firmware-host", "firmware-hwcdc", "firmware-tinyusb",
    "deployment", "docs-notice", "post-push-verifier-unit", "public-audit",
]

# Deterministic 64-lowercase-hex lab digest for the synthetic live preview.
_LIVE_PREVIEW_SHA256 = "cdef0123" * 8


@pytest.fixture
def complete_evidence(valid_hardware_report, request) -> dict:
    """A fully-green, secret-free evidence bundle for the approve-build happy path.

    Every gate the builder must pass is set to its clean value: the clean-room
    tree is reported clean, all twelve CI stages exit 0, the public audit has zero
    findings, hardware acceptance is complete (every check ``true``), the
    live-preview digest equals its canonical manifest digest, preserved
    before/after blobs are identical (a clean target dry-run), and the proposed
    commit list holds exactly the one replacement commit. The manifest field is
    the real tracked-release software manifest, and candidate.diff / commits.txt
    are synthetic but secret-free so no real worktree content (which may carry
    the token literal in release tooling) can leak into the test bundle.
    """
    import hashlib
    from pathlib import Path

    root = Path(request.config.rootdir) if request.config.rootdir else Path(__file__).parents[2]
    manifest = [
        ln for ln in (root / "release" / "software-files.txt").read_text(encoding="utf-8").splitlines()
        if ln != ""
    ]

    stage_with_sha = {
        stage: {
            "exit_code": 0,
            "duration_s": 0.0,
            "log_sha256": hashlib.sha256(stage.encode("utf-8")).hexdigest(),
        }
        for stage in CI_STAGES
    }
    preserved = {"firmware": "target", "revision": "clean-room"}
    commit_hashes = ["5" * 40, "6" * 40, "7" * 40]
    commit_subjects = [
        "feat: seed clean-room MicroPad control surface",
        "fix: reconcile MQTT contract with backend",
        "docs: add English release and safety guides",
    ]
    return {
        "repo": {"clean": True},
        "test-evidence": copy.deepcopy(stage_with_sha),
        "public-audit": {"clean": True, "files_scanned": len(manifest), "findings": {}},
        "hardware-summary": redact_summary(valid_hardware_report),
        "live-ha-preview": {
            "digest": _LIVE_PREVIEW_SHA256,
            "manifest_sha256": _LIVE_PREVIEW_SHA256,
            "topics": ["micropad/catalog/set", "micropad/page/set", "micropad/keymap/set"],
        },
        "target-preview": {
            "preserved_before": dict(preserved),
            "preserved_after": dict(preserved),
        },
        "proposed-commits": ["feat: replace software with clean-room MicroPad implementation"],
        "candidate-diff": (
            "diff --git a/firmware/micropad_core.cpp b/firmware/micropad_core.cpp\n"
            "new file mode 100644\n"
            "index 0000000..bbbbbbb\n"
            "--- /dev/null\n"
            "+++ b/firmware/micropad_core.cpp\n"
            "@@ -0,0 +1,2 @@\n"
            "+safeCopy(value.mqttPassword, \"replace-me\");\n"
            "+// clean-room replacement\n"
        ),
        "commits": [
            f"{commit_hashes[i]} {commit_subjects[i]}" for i in range(len(commit_hashes))
        ],
        "manifest": manifest,
    }


# --- Integration Task 10 fixtures: local bare target repo + clean source ------
#
# ``fake_target_repo`` is a two-commit LOCAL Git repository (no network, no
# personal data) meant to stand in for the public GitHub target during the
# dry-run integration tests. It holds synthetic hardware assets, a license and
# an old firmware file, exposes a local bare-clone URL (``.url``), and a
# ``snapshot(prefixes)`` helper returning every committed blob hash underneath
# the given path prefixes (computed with ``git hash-object`` — never by reading
# the fixtures' source text into the test).
#
# ``clean_source`` is a minimal clean-room source tree: an explicit two-line
# manifest plus a noticed README and a firmware source file. Neither fixture
# contains any real person, path, digest, network address or credential.

_LICENSE_NAMES = {"license", "license.txt", "license.md", "copying", "copying.txt"}

# (relative_path, content, commit_group) — 3 defaults in commit 1, rest in commit 2.
_DEFAULT_TARGET_FILES = [
    ("README.md", "# Seed target\n", 1),
    ("old_firmware.ino", "void setup(){}\nvoid loop(){}\n", 1),
    ("PCB/board.kicad_pcb", "(kicad_pcb synthetic board)\n", 2),
    ("STL Files/case.stl", "solid case end\n", 2),
    ("LICENSE", "MIT (synthetic clean-room fixture)\n", 2),
]

_CLEAN_MANIFEST_LINES = ["README.md", "firmware/MicroPad_HA_Controller.ino"]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


@dataclass
class FakeTargetRepo:
    """A synthetic two-commit local target repo plus its bare clone URL."""

    workdir: Path
    bare: Path
    url: str

    def snapshot(self, prefixes: list[str]) -> dict[str, str]:
        out = _git(self.workdir, "ls-files", "-z").stdout
        paths = [p for p in out.split("\0") if p]
        result: dict[str, str] = {}
        for path in paths:
            matched = any(
                path == prefix or path.startswith(prefix + "/") for prefix in prefixes
            ) or Path(path).name.lower() in _LICENSE_NAMES
            if not matched:
                continue
            sha = _git(self.workdir, "hash-object", "--", path).stdout.strip()
            result[path] = sha
        return result

    def advance_main(self) -> None:
        """Add a commit to the bare remote's ``main`` past any existing checkout."""
        _git(self.workdir, "commit", "--allow-empty", "-m", "advance target main")
        _git(self.workdir, "push", str(self.bare), "main:main")


def _make_fake_target(tmp_path: Path, files: list[tuple[str, str, int]]) -> FakeTargetRepo:
    workdir = tmp_path / "target"
    workdir.mkdir()
    _git(workdir, "init", "--initial-branch", "main")
    _git(workdir, "config", "user.email", "cleanroom@example.test")
    _git(workdir, "config", "user.name", "Clean Room")
    _git(workdir, "config", "commit.gpgsign", "false")
    for group in sorted({g for _, _, g in files}):
        for relpath, content, grp in files:
            if grp == group:
                leaf = workdir / relpath
                leaf.parent.mkdir(parents=True, exist_ok=True)
                leaf.write_text(content, encoding="utf-8")
        _git(workdir, "add", "-A")
        _git(workdir, "commit", "-m", f"target commit {group}")
    bare = tmp_path / "target-bare"
    subprocess.run(
        ["git", "clone", "--bare", str(workdir), str(bare)],
        check=True, capture_output=True, text=True,
    )
    return FakeTargetRepo(workdir=workdir, bare=bare, url=str(bare))


@pytest.fixture
def target_repo_factory(tmp_path) -> callable:
    def _build(files: list[tuple[str, str, int]] | None = None) -> FakeTargetRepo:
        return _make_fake_target(tmp_path, files or _DEFAULT_TARGET_FILES)

    return _build


@pytest.fixture
def fake_target_repo(target_repo_factory) -> FakeTargetRepo:
    """Default two-commit target with PCB/, STL Files/, LICENSE, README.md, firmware."""
    return target_repo_factory()


@pytest.fixture
def clean_source(tmp_path) -> Path:
    """Minimal clean-room source: explicit 2-line manifest + noticed files."""
    source = tmp_path / "source"
    (source / "release").mkdir(parents=True)
    (source / "release" / "software-files.txt").write_text(
        "\n".join(_CLEAN_MANIFEST_LINES) + "\n", encoding="utf-8"
    )
    (source / "README.md").write_text("# Clean-room MicroPad\n", encoding="utf-8")
    (source / "firmware").mkdir(parents=True)
    (source / "firmware" / "MicroPad_HA_Controller.ino").write_text(
        "// clean-room firmware\n", encoding="utf-8"
    )
    return source


# --- Integration Task 11 fixture: authoritative post-push snapshot ---------


@pytest.fixture
def valid_snapshot() -> dict:
    """Complete synthetic post-push evidence with no operator or network data."""
    base_sha = "1" * 40
    final_sha = "2" * 40
    tree_sha256 = "3" * 64
    preserved = {
        "LICENSE": "4" * 40,
        "PCB/board.kicad_pcb": "5" * 40,
        "STL Files/case.stl": "6" * 40,
    }
    manifest_paths = ["README.md", "firmware/MicroPad_HA_Controller.ino"]
    all_paths = sorted([*manifest_paths, *preserved])
    return {
        "commit_count": 1,
        "base_sha": base_sha,
        "local_sha": final_sha,
        "github_main_sha": final_sha,
        "parent_shas": [base_sha],
        "candidate_tree_sha256": tree_sha256,
        "anonymous_tree_sha256": tree_sha256,
        "github_tree_sha256": tree_sha256,
        "manifest_paths": manifest_paths,
        "candidate_paths": list(all_paths),
        "anonymous_paths": list(all_paths),
        "github_paths": list(all_paths),
        "preserved_before": dict(preserved),
        "preserved_after": dict(preserved),
        "public_audit_ok": True,
        "github_tree_complete": True,
        "fetch_urls": ["https://github.com/example/project.git"],
        "push_urls": ["git@github.com:example/project.git"],
    }