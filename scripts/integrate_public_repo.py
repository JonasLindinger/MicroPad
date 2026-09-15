#!/usr/bin/env python3
# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""READ-ONLY dry-run integration of the clean-room software into a public target.

Clones the public target repository (``https://github.com/JonasLindinger/MicroPad.git``
by default) fresh, then rewrites its working tree in place — WITHOUT committing,
reconfiguring its remote, or pushing — so that:

* the target's ``.git`` **history is untouched** — exactly ``origin/main`` is
  checked out and the working tree is left uncommitted;
* the **hardware assets** (everything under ``PCB/`` and ``STL Files/``) and the
  **existing project license** (exactly one case-insensitive
  ``LICENSE``/``LICENSE.txt``/``LICENSE.md``/``COPYING``/``COPYING.txt`` file) are
  preserved **byte-for-byte**, verified by ``git hash-object`` before and after;
* every other old software tracked file is removed (``git rm`` with an argument
  array — never a shell), the clean-room manifest files from
  ``<source>/release/software-files.txt`` are copied in with ``shutil.copy2``,
  and the final candidate set is verified to equal *preserved + manifest* only.

The module never reads the *contents* of removed old software files: it only
enumerates tracked paths with ``git ls-files -z`` and hashes preserved blobs. It
never calls ``git commit``, ``git remote set-url``, or ``git push``.

Evidence is written to a sibling ``--evidence`` directory (e.g.
``build/public-integration-evidence/``), never inside the target checkout:
``base.sha``, ``preserved-before.json``, ``preserved-after.json``,
``removed-paths.txt``, ``added-paths.txt``, ``target-tree.txt``,
``candidate.diff``, and (as a pending stub, populated later by
``scripts/audit_public_release.py``) ``public-audit.json``.

``candidate.diff`` is deliberately **not** a raw target Git diff. It is a
deterministic sequence of synthetic new-file patches from ``/dev/null``, one
for every clean-room manifest path, generated exclusively from clean-room
source bytes. Empty files receive an explicit header. Deletions are represented
only by path metadata in ``removed-paths.txt``; preserved target bytes are
represented by their hashes. This boundary records the exact replacement while
making it impossible for deleted/replaced old-software content to enter evidence.

Hard gates (any failure aborts before evidence is finalized): preserved
before/after blob hashes are identical, the checkout has **zero local commits
beyond fetched ``origin/main``** (HEAD == origin/main, and origin/main has not
advanced between fetch and preparation), clean-room addition patches pass
``git diff --check``, the
destination is absent or an empty directory before this function creates its
fresh clone, and the manifest lists no path outside the source and no symlink.

Usage:
    scripts/integrate_public_repo.py --source "$PWD" \
        --destination build/public-integration \
        --evidence build/public-integration-evidence --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

# Relative path (inside the clean-room source) of the tracked-release manifest.
MANIFEST_RELPATH = Path("release") / "software-files.txt"
# Hardware asset top-level directories that must be preserved unconditionally.
PRESERVE_DIRS = ("PCB", "STL Files")
# Exactly one case-insensitive license/COPYING basename is preserved.
LICENSE_NAMES = frozenset({"license", "license.txt", "license.md", "copying", "copying.txt"})
# Repository metadata / local state never counted as candidate content.
_EXCLUDE_DIRS = frozenset({".git"})
# Git subcommands this tool is forbidden from calling under any path.
_FORBIDDEN_SUBCOMMANDS = ("commit", "push", "remote")

# The exact externally-managed replacement commit message (used by IT11).
REPLACEMENT_COMMIT_MESSAGE = (
    "feat: replace software with clean-room MicroPad implementation\n"
    "\n"
    "Replace firmware, Home Assistant automation, configurator, tests,\n"
    "deployment assets, README, and software documentation with the reviewed\n"
    "clean-room implementation. Preserve repository history, PCB and STL assets,\n"
    "and the existing project license.\n"
)


class IntegrationError(RuntimeError):
    """Fatal integration precondition/verification failure (non-networked)."""


@dataclass(frozen=True)
class ReplacementResult:
    """Outcome summary of a (always uncommitted) dry-run candidate preparation."""

    destination: Path
    base_sha: str = ""
    commit_count_added: int = 0
    preserved_before: dict = field(default_factory=dict)
    preserved_after: dict = field(default_factory=dict)
    removed_paths: tuple = ()
    added_paths: tuple = ()
    target_tree: tuple = ()
    unexpected_paths: tuple = ()


def _run(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git subcommand inside ``repo`` with an argument array (no shell).

    The tool may only issue git housekeeping/read commands; any attempt to reach
    a forbidden subcommand is refused up front.
    """
    if args and args[0] in _FORBIDDEN_SUBCOMMANDS:
        raise IntegrationError(f"git {args[0]} is forbidden for a read-only dry run")
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check, capture_output=True, text=True,
    )


def _git_pipe(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


def _tracked_paths(repo: Path) -> list[str]:
    out = _git_pipe(repo, "ls-files", "-z").stdout
    return [p for p in out.split("\0") if p]


def _blob_sha(repo: Path, path: str) -> str:
    """Git blob hash of a preserved tracked blob, before removal of other files."""
    return _git_pipe(repo, "hash-object", "--", path).stdout.strip()


def _is_license(name: str) -> bool:
    return name.lower() in LICENSE_NAMES


def _classify_preserved(repo: Path, tracked: list[str]) -> tuple[dict[str, str], tuple[str, ...]]:
    """Return (path->blob_sha, preserved_paths) for hardware + the single license."""
    dir_preserved = {
        p for p in tracked
        if any(p.startswith(d + "/") for d in PRESERVE_DIRS)
    }
    if not any(p.startswith("PCB/") for p in dir_preserved):
        raise IntegrationError("target has no preserved PCB/ hardware assets")
    if not any(p.startswith("STL Files/") for p in dir_preserved):
        raise IntegrationError("target has no preserved 'STL Files/' hardware assets")

    license_candidates = [p for p in tracked if _is_license(Path(p).name)]
    if not license_candidates:
        raise IntegrationError("target has no license (expected exactly one LICENSE/COPYING file)")
    if len(license_candidates) > 1:
        raise IntegrationError(f"target has {len(license_candidates)} license candidates; expected exactly one")

    preserved_paths = tuple(sorted(dir_preserved | set(license_candidates)))
    return {p: _blob_sha(repo, p) for p in preserved_paths}, preserved_paths


def _validate_manifest_entries(source: Path, manifest: list[str]) -> tuple[str, ...]:
    """Normalize portable paths, then reject absolute/escaping paths and symlinks."""
    source_resolved = source.resolve()
    normalized_entries: list[str] = []
    for entry in manifest:
        # A release manifest is Git/path-platform independent. Treat both slash
        # styles as separators before security checks so a Windows-style entry
        # cannot bypass a namespace or traversal guard on POSIX (or vice versa).
        portable_entry = entry.replace("\\", "/")
        windows_entry = PureWindowsPath(entry)
        if PurePosixPath(portable_entry).is_absolute() or windows_entry.drive:
            raise IntegrationError(f"manifest path escapes the source (rejected): {entry}")
        normalized = posixpath.normpath(portable_entry)
        candidate = source / normalized
        if candidate.is_symlink():
            raise IntegrationError(f"manifest path is a symlink (rejected): {entry}")
        if not candidate.resolve().is_relative_to(source_resolved):
            raise IntegrationError(f"manifest path escapes the source (rejected): {entry}")
        if not candidate.is_file():
            raise IntegrationError(f"manifest path is not a regular file: {entry}")
        normalized_entries.append(PurePosixPath(normalized).as_posix())
    return tuple(normalized_entries)


def _read_manifest(source: Path) -> list[str]:
    manifest_file = source / MANIFEST_RELPATH
    if not manifest_file.is_file():
        raise IntegrationError(f"manifest not found: {manifest_file}")
    lines = [
        ln.strip()
        for ln in manifest_file.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    return lines


def _copy_manifest(source: Path, destination: Path, manifest: tuple[str, ...]) -> None:
    for entry in manifest:
        src = source / entry
        dst = destination / entry
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _candidate_files(destination: Path) -> set[str]:
    """Every file under the target tree, excluding repository metadata."""
    result: set[str] = set()
    for root, dirs, files in os.walk(destination):
        rel_root = Path(root).relative_to(destination)
        dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS]
        for name in files:
            result.add((rel_root / name).as_posix())
    return result


def _ensure_target(destination: Path, clone_url: str) -> None:
    """Clone into a destination that is absent or an existing empty directory."""
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise IntegrationError(
                f"fresh destination must be absent or empty before clone: {destination}"
            )
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--single-branch", "--branch", "main", "--",
         clone_url, str(destination)],
        check=True, capture_output=True, text=True,
    )


def _cleanroom_candidate_diff(source: Path, manifest: tuple[str, ...]) -> str:
    """Represent exact manifest bytes without ever reading target software bytes.

    Non-empty files use Git's deterministic ``/dev/null`` addition format,
    including a literal binary patch when needed. Git return code 1 means a
    difference was emitted. Empty files need a synthetic header because an
    empty file and ``/dev/null`` otherwise compare equal.
    """
    sections: list[str] = []
    for entry in manifest:
        src = source / entry
        if src.stat().st_size == 0:
            mode = "100755" if src.stat().st_mode & 0o111 else "100644"
            sections.append(
                f"diff --git a/{entry} b/{entry}\n"
                f"new file mode {mode}\n"
                "index 0000000..e69de29\n"
            )
            continue
        checked = subprocess.run(
            ["git", "-C", str(source), "diff", "--no-index", "--check",
             "--", "/dev/null", entry],
            check=False, capture_output=True,
        )
        if checked.returncode != 1:
            detail = checked.stdout.decode("utf-8", errors="replace").strip()
            raise IntegrationError(
                f"clean-room addition patch failed whitespace validation for {entry!r} "
                f"(git diff exit {checked.returncode}): {detail}"
            )
        completed = subprocess.run(
            ["git", "-C", str(source), "diff", "--no-index", "--binary",
             "--", "/dev/null", entry],
            check=False, capture_output=True,
        )
        if completed.returncode != 1:
            raise IntegrationError(
                f"could not build clean-room addition patch for {entry!r} "
                f"(git diff exit {completed.returncode})"
            )
        sections.append(completed.stdout.decode("utf-8"))
    return "".join(sections)


def _write_evidence(
    evidence_dir: Path,
    base_sha: str,
    preserved_before: dict[str, str],
    preserved_after: dict[str, str],
    removed_paths: tuple[str, ...],
    added_paths: tuple[str, ...],
    candidate: str,
) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "base.sha").write_text(base_sha + "\n", encoding="utf-8")
    (evidence_dir / "preserved-before.json").write_text(
        json.dumps(preserved_before, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (evidence_dir / "preserved-after.json").write_text(
        json.dumps(preserved_after, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (evidence_dir / "removed-paths.txt").write_text(
        "\n".join(removed_paths) + ("\n" if removed_paths else ""), encoding="utf-8"
    )
    (evidence_dir / "added-paths.txt").write_text(
        "\n".join(added_paths) + "\n", encoding="utf-8"
    )
    (evidence_dir / "candidate.diff").write_text(candidate, encoding="utf-8")
    if not (evidence_dir / "public-audit.json").exists():
        # A pending stub; audit_public_release.py (release Step 6) overwrites it.
        (evidence_dir / "public-audit.json").write_text(
            json.dumps({"clean": None, "status": "pending",
                        "note": "populated by scripts/audit_public_release.py (Step 6)"},
                       indent=2) + "\n", encoding="utf-8"
        )


def prepare_replacement(
    source: Path | str,
    destination: Path | str,
    clone_url: str,
    dry_run: bool = True,
    evidence: Path | str | None = None,
) -> ReplacementResult:
    """Prepare (uncommitted) a fresh target candidate = preserved + clean-room manifest.

    This is strictly read-only with respect to the target: it never commits,
    never reconfigures the remote, and never pushes. ``evidence`` defaults to the
    sibling ``<destination>.evidence`` directory and is never written inside the
    target checkout.
    """
    if not dry_run:
        raise IntegrationError("integration is read-only; the actual push happens in IT11")

    source = Path(source).expanduser()
    destination = Path(destination).expanduser()
    evidence_dir = Path(evidence).expanduser() if evidence else (
        destination.parent / (destination.name + ".evidence")
        if not destination.as_posix().startswith("build/")
        else destination.parent / "public-integration-evidence"
    )

    # Evidence must live OUTSIDE the target checkout.
    dest_resolved = destination.resolve()
    if evidence_dir.resolve() != dest_resolved and (
        evidence_dir.resolve().is_relative_to(dest_resolved)
        or dest_resolved.is_relative_to(evidence_dir.resolve())
    ):
        raise IntegrationError("evidence directory must be outside the target checkout")

    _ensure_target(destination, clone_url)

    # base.sha / zero-local-commits guard: HEAD must equal fetched origin/main.
    base_sha = _git_pipe(destination, "rev-parse", "--verify", "origin/main").stdout.strip()
    head_sha = _git_pipe(destination, "rev-parse", "--verify", "HEAD").stdout.strip()
    if head_sha != base_sha:
        raise IntegrationError(
            f"checkout diverged from fetched origin/main: local HEAD {head_sha} != {base_sha}"
        )
    commit_count_added = int(
        _git_pipe(destination, "rev-list", "--count", "origin/main..HEAD").stdout.strip()
    )

    tracked = _tracked_paths(destination)
    preserved_before, preserved_paths = _classify_preserved(destination, tracked)

    manifest = _read_manifest(source)
    manifest = _validate_manifest_entries(source, manifest)
    preserved_overlap = sorted({
        entry
        for entry in manifest
        if entry in preserved_paths
        or any(entry == directory or entry.startswith(directory + "/")
               for directory in PRESERVE_DIRS)
    })
    if preserved_overlap:
        raise IntegrationError(
            "manifest overlaps preserved target paths: " + ", ".join(preserved_overlap)
        )

    removed_paths = tuple(sorted(set(tracked) - set(preserved_paths)))
    for path in removed_paths:  # refuse any preserved path that became a symlink
        if (destination / path).is_symlink():
            raise IntegrationError(f"preserved path is a symlink (rejected): {path}")

    # Remove every other tracked path (argument array; never a shell).
    if removed_paths:
        _run(destination, "rm", "--quiet", "--", *removed_paths)

    _copy_manifest(source, destination, manifest)
    added_paths = manifest

    # Verify the final candidate set == preserved + manifest, byte-for-byte.
    preserved_after = {p: _blob_sha(destination, p) for p in preserved_paths}
    preservation_mismatches = [
        path
        for path in preserved_paths
        if preserved_before.get(path) != preserved_after.get(path)
    ]
    if preservation_mismatches:
        details = ", ".join(
            f"{path}: {preserved_before.get(path, '<missing>')} -> "
            f"{preserved_after.get(path, '<missing>')}"
            for path in preservation_mismatches
        )
        raise IntegrationError(f"preserved blob hash mismatch: {details}")

    expected = set(preserved_paths) | set(added_paths)
    actual = _candidate_files(destination)
    unexpected = (actual - expected) | (expected - actual)

    # Clean-room-safe review evidence: additions come only from source manifest
    # bytes. Old target software is represented solely by removed path names.
    candidate_diff = _cleanroom_candidate_diff(source, manifest)

    # TOCTOU guard: perform a real final remote query after all preparation, then
    # compare the freshly updated remote-tracking ref with the clone-time base.
    _git_pipe(destination, "fetch", "origin", "main:refs/remotes/origin/main")
    base_now = _git_pipe(destination, "rev-parse", "--verify", "origin/main").stdout.strip()
    if base_now != base_sha:
        raise IntegrationError(f"target main advanced during preparation ({base_sha} -> {base_now})")

    tree = tuple(sorted(actual))
    result = ReplacementResult(
        destination=destination,
        base_sha=base_sha,
        commit_count_added=commit_count_added,
        preserved_before=preserved_before,
        preserved_after=preserved_after,
        removed_paths=removed_paths,
        added_paths=added_paths,
        target_tree=tree,
        unexpected_paths=tuple(sorted(unexpected)),
    )

    if unexpected:
        raise IntegrationError(
            "candidate does not equal preserved + manifest; unexpected paths: "
            + ", ".join(sorted(unexpected))
        )

    _write_evidence(
        evidence_dir, base_sha, preserved_before, preserved_after,
        removed_paths, added_paths, candidate_diff,
    )
    (evidence_dir / "target-tree.txt").write_text("\n".join(tree) + "\n", encoding="utf-8")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True, type=Path,
                        help="clean-room source tree (contains release/software-files.txt)")
    parser.add_argument("--destination", required=True, type=Path,
                        help="target checkout to prepare (cloned, left uncommitted)")
    parser.add_argument("--evidence", type=Path, default=None,
                        help="sibling evidence dir (default: build/public-integration-evidence)")
    parser.add_argument("--clone-url", default="https://github.com/JonasLindinger/MicroPad.git",
                        help="public target clone URL (default: MicroPad)")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="read-only preparation; this tool never commits or pushes")
    args = parser.parse_args(argv)
    prepare_replacement(
        source=args.source,
        destination=args.destination,
        clone_url=args.clone_url,
        dry_run=args.dry_run,
        evidence=args.evidence,
    )
    print(f"integrate_public_repo: prepared uncommitted candidate at {args.destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())