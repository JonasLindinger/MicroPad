#!/usr/bin/env python3
# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Verify an already-pushed public replacement from local, read-only evidence.

This tool performs no network request and no write to Git, GitHub, Home Assistant,
or MQTT. It consumes GitHub API JSON captured by the release operator and compares
it with a candidate checkout, a fresh anonymous checkout, Task 10 evidence, and the
clean-room source manifest. Exact content is committed by sorted path/mode/Git-blob
records; deleted old software is represented only by path metadata. In particular,
the tool never regenerates or compares a raw target diff, because doing so would
re-materialize old software content and violate the clean-room boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qsl, urlsplit

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_BLOB_MODES = frozenset({"100644", "100755"})
_TREE_MODE = "040000"
_EMBEDDED_SECRET_MARKERS = re.compile(r"(?i)(x-access-token|gh[pousr]_[a-z0-9]+)")
_CREDENTIAL_KEYS = re.compile(
    r"(?i)^(access[_-]?token|auth[_-]?token|token|password|passwd|credential)$"
)


class VerificationError(ValueError):
    """An authoritative release assertion diverged or was malformed."""


def _fail(field: str, detail: str = "") -> None:
    suffix = f": {detail}" if detail else ""
    raise VerificationError(field + suffix)


def _sha(value: object, field: str, length: int = 40) -> str:
    pattern = _HEX40 if length == 40 else _HEX64
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail(field, f"expected {length}-lowercase-hex")
    return value


def _safe_path(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        _fail(field, "invalid repository path")
    normalized = posixpath.normpath(value)
    if normalized != value or PurePosixPath(value).is_absolute() or normalized in {".", ".."} or normalized.startswith("../"):
        _fail(field, "non-canonical repository path")
    return value


def _path_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list):
        _fail(field, "expected a list")
    result = [_safe_path(item, field) for item in value]
    if len(result) != len(set(result)):
        _fail(field, "duplicate path")
    return result


def _preserved(value: object, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        _fail(field, "expected a non-empty path-to-blob map")
    result: dict[str, str] = {}
    for path, blob in value.items():
        result[_safe_path(path, field)] = _sha(blob, field)
    return result


def _credential_free_urls(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        _fail(field, "expected at least one remote URL")
    result: list[str] = []
    for raw in value:
        if not isinstance(raw, str) or not raw or any(char.isspace() for char in raw):
            _fail(field, "invalid remote URL")
        if _EMBEDDED_SECRET_MARKERS.search(raw):
            _fail(field, "credential marker in remote URL")
        parsed = urlsplit(raw)
        if parsed.scheme in {"http", "https", "ssh", "git"}:
            if parsed.password is not None:
                _fail(field, "password-bearing remote URL")
            if parsed.scheme in {"http", "https"} and parsed.username is not None:
                _fail(field, "userinfo-bearing remote URL")
            for key, _unused in parse_qsl(parsed.query, keep_blank_values=True):
                if _CREDENTIAL_KEYS.fullmatch(key):
                    _fail(field, "credential query in remote URL")
        result.append(raw)
    return result


def verify_snapshot(snapshot: dict[str, object]) -> dict[str, object]:
    """Pure validation of a complete post-push snapshot; return it unchanged."""
    if not isinstance(snapshot, dict):
        _fail("snapshot", "expected object")
    required = {
        "commit_count", "base_sha", "local_sha", "github_main_sha", "parent_shas",
        "candidate_tree_sha256", "anonymous_tree_sha256", "github_tree_sha256",
        "manifest_paths", "candidate_paths", "anonymous_paths", "github_paths",
        "preserved_before", "preserved_after", "public_audit_ok",
        "github_tree_complete", "fetch_urls", "push_urls",
    }
    missing = sorted(required - snapshot.keys())
    if missing:
        _fail(missing[0], "missing")
    if type(snapshot["commit_count"]) is not int or snapshot["commit_count"] != 1:
        _fail("commit_count")

    base_sha = _sha(snapshot["base_sha"], "base_sha")
    local_sha = _sha(snapshot["local_sha"], "local_sha")
    github_sha = _sha(snapshot["github_main_sha"], "github_main_sha")
    parents = snapshot["parent_shas"]
    if not isinstance(parents, list) or len(parents) != 1:
        _fail("parent_shas", "final commit must have exactly one parent")
    if _sha(parents[0], "parent_shas") != base_sha:
        _fail("parent_shas", "sole parent does not equal base_sha")
    if local_sha != github_sha:
        _fail("github_main_sha")

    candidate_digest = _sha(snapshot["candidate_tree_sha256"], "candidate_tree_sha256", 64)
    anonymous_digest = _sha(snapshot["anonymous_tree_sha256"], "anonymous_tree_sha256", 64)
    github_digest = _sha(snapshot["github_tree_sha256"], "github_tree_sha256", 64)
    if candidate_digest != anonymous_digest:
        _fail("anonymous_tree_sha256")
    if candidate_digest != github_digest:
        _fail("github_tree_sha256")

    manifest = _path_list(snapshot["manifest_paths"], "manifest_paths")
    before = _preserved(snapshot["preserved_before"], "preserved_before")
    after = _preserved(snapshot["preserved_after"], "preserved_after")
    if before != after:
        _fail("preserved_after")
    if set(manifest) & set(before):
        _fail("manifest_paths", "overlaps preserved paths")
    expected = set(manifest) | set(before)
    for field in ("candidate_paths", "anonymous_paths", "github_paths"):
        paths = _path_list(snapshot[field], field)
        if set(paths) != expected:
            _fail(field, "must equal manifest plus preserved paths exactly")

    if snapshot["github_tree_complete"] is not True:
        _fail("github_tree_complete")
    if snapshot["public_audit_ok"] is not True:
        _fail("public_audit_ok")
    _credential_free_urls(snapshot["fetch_urls"], "fetch_urls")
    _credential_free_urls(snapshot["push_urls"], "push_urls")
    return snapshot


def parse_github_branch(value: object) -> tuple[str, str, list[str]]:
    """Strictly extract final SHA, root tree SHA, and parents from branch JSON."""
    try:
        if not isinstance(value, dict):
            raise TypeError
        commit = value["commit"]
        if not isinstance(commit, dict):
            raise TypeError
        final_sha = _sha(commit["sha"], "github_main")
        detail = commit["commit"]
        if not isinstance(detail, dict) or not isinstance(detail["tree"], dict):
            raise TypeError
        tree_sha = _sha(detail["tree"]["sha"], "github_main")
        raw_parents = commit["parents"]
        if not isinstance(raw_parents, list) or len(raw_parents) != 1:
            raise TypeError
        parents = [_sha(parent["sha"], "github_main") for parent in raw_parents if isinstance(parent, dict)]
        if len(parents) != 1:
            raise TypeError
    except (KeyError, TypeError, VerificationError):
        _fail("github_main", "malformed branch response")
    return final_sha, tree_sha, parents


def parse_github_tree(value: object) -> tuple[str, list[tuple[str, str, str]], int]:
    """Strictly parse a complete recursive GitHub tree into canonical blob records."""
    if not isinstance(value, dict) or value.get("truncated") is not False:
        _fail("github_tree_complete")
    try:
        root_sha = _sha(value["sha"], "github_tree")
        entries = value["tree"]
    except (KeyError, VerificationError):
        _fail("github_tree", "malformed recursive tree")
    if not isinstance(entries, list):
        _fail("github_tree", "tree must be a list")

    seen: set[str] = set()
    blobs: list[tuple[str, str, str]] = []
    tree_paths: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            _fail("github_tree", "entry must be an object")
        try:
            path = _safe_path(entry["path"], "github_tree")
            mode = entry["mode"]
            kind = entry["type"]
            sha = _sha(entry["sha"], "github_tree")
        except (KeyError, VerificationError):
            _fail("github_tree", "malformed entry")
        if path in seen:
            _fail("github_tree", "duplicate path")
        seen.add(path)
        if kind == "blob" and mode in _BLOB_MODES:
            blobs.append((path, mode, sha))
        elif kind == "tree" and mode == _TREE_MODE:
            tree_paths.add(path)
        else:
            _fail("github_tree", "unsupported type or mode")

    expected_trees = {
        parent.as_posix()
        for path, _mode, _sha_value in blobs
        for parent in PurePosixPath(path).parents
        if parent.as_posix() != "."
    }
    if tree_paths != expected_trees:
        _fail("github_tree", "recursive directory entries are incomplete or extra")
    return root_sha, sorted(blobs), len(entries)


def canonical_tree_digest(records: Sequence[tuple[str, str, str]]) -> str:
    """SHA-256 over sorted, unambiguous path/mode/blob records."""
    checked: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for path, mode, blob_sha in records:
        path = _safe_path(path, "tree_records")
        if path in seen or mode not in _BLOB_MODES:
            _fail("tree_records", "duplicate path or unsupported mode")
        seen.add(path)
        checked.append((path, mode, _sha(blob_sha, "tree_records")))
    payload = b"".join(
        path.encode("utf-8") + b"\0" + mode.encode("ascii") + b"\0" + blob.encode("ascii") + b"\n"
        for path, mode, blob in sorted(checked)
    )
    return hashlib.sha256(payload).hexdigest()


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        _fail("git", f"command failed in {repo}: {args[0] if args else ''}")
        raise AssertionError from exc


def _git_records(repo: Path, revision: str) -> list[tuple[str, str, str]]:
    raw = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-rz", revision],
        check=True, capture_output=True,
    ).stdout
    records: list[tuple[str, str, str]] = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        try:
            metadata, path_bytes = item.split(b"\t", 1)
            mode_b, kind_b, sha_b = metadata.split(b" ")
            path = path_bytes.decode("utf-8")
            mode, kind, blob_sha = mode_b.decode(), kind_b.decode(), sha_b.decode()
        except (ValueError, UnicodeDecodeError):
            _fail("tree_records", "malformed git ls-tree output")
        if kind != "blob" or mode not in _BLOB_MODES:
            _fail("tree_records", "candidate contains symlink, submodule, or unsupported mode")
        records.append((_safe_path(path, "tree_records"), mode, _sha(blob_sha, "tree_records")))
    if len(records) != len({record[0] for record in records}):
        _fail("tree_records", "duplicate path")
    return sorted(records)


def _git_base_objects(repo: Path, revision: str) -> dict[str, str]:
    """Enumerate base path/object IDs without opening any old software content.

    Unlike a final public tree, the historical target may legitimately contain
    symlinks or submodules that are about to be removed. Only canonical path and
    object metadata are consumed here; modes and object types are not carried
    into release content.
    """
    raw = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-rz", revision],
        check=True, capture_output=True,
    ).stdout
    result: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if not item:
            continue
        try:
            metadata, path_bytes = item.split(b"\t", 1)
            mode_b, kind_b, sha_b = metadata.split(b" ")
            path = _safe_path(path_bytes.decode("utf-8"), "base_tree")
            mode, kind = mode_b.decode(), kind_b.decode()
            object_sha = _sha(sha_b.decode(), "base_tree")
        except (ValueError, UnicodeDecodeError, VerificationError):
            _fail("base_tree", "malformed git ls-tree output")
        if re.fullmatch(r"[0-7]{6}", mode) is None or kind not in {"blob", "commit"}:
            _fail("base_tree", "unsupported object metadata")
        if path in result:
            _fail("base_tree", "duplicate path")
        result[path] = object_sha
    return result


def _json(path: Path, field: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _fail(field, f"invalid JSON file: {path}")


def _lines(path: Path, field: str) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        _fail(field, f"cannot read {path}")
    if text and not text.endswith("\n"):
        _fail(field, "missing final newline")
    lines = text.splitlines()
    return _path_list(lines, field)


def _remote_urls(repo: Path, push: bool) -> list[str]:
    args = ("remote", "get-url", "--push", "--all", "origin") if push else ("remote", "get-url", "--all", "origin")
    urls = _git(repo, *args).splitlines()
    return _credential_free_urls(urls, "push_urls" if push else "fetch_urls")


def build_snapshot(
    candidate: Path,
    evidence: Path,
    anonymous: Path,
    source: Path,
    manifest_file: Path,
    github_main_file: Path,
    github_tree_file: Path,
    public_audit_file: Path,
) -> dict[str, object]:
    """Build and verify the snapshot from local repositories and captured JSON."""
    base_sha = _sha((evidence / "base.sha").read_text(encoding="utf-8").strip(), "base_sha")
    local_sha = _sha(_git(candidate, "rev-parse", "HEAD").strip(), "local_sha")
    anonymous_sha = _sha(_git(anonymous, "rev-parse", "HEAD").strip(), "anonymous_sha")
    if anonymous_sha != local_sha:
        _fail("anonymous_sha")
    commit_count_text = _git(candidate, "rev-list", "--count", f"{base_sha}..{local_sha}").strip()
    try:
        commit_count = int(commit_count_text)
    except ValueError:
        _fail("commit_count")
    parent_line = _git(candidate, "rev-list", "--parents", "-n", "1", local_sha).split()
    parent_shas = parent_line[1:]

    branch_sha, branch_tree_sha, branch_parents = parse_github_branch(_json(github_main_file, "github_main"))
    github_root_sha, github_records, github_entry_count = parse_github_tree(_json(github_tree_file, "github_tree"))
    if branch_tree_sha != github_root_sha:
        _fail("github_tree", "root SHA differs from branch commit tree")
    if branch_parents != parent_shas:
        _fail("parent_shas", "GitHub parents differ from candidate")

    manifest_paths = _lines(manifest_file, "manifest_paths")
    if manifest_paths != sorted(manifest_paths, key=lambda item: item.encode("utf-8")):
        _fail("manifest_paths", "manifest is not bytewise sorted")
    candidate_records = _git_records(candidate, local_sha)
    anonymous_records = _git_records(anonymous, anonymous_sha)

    source_records: dict[str, tuple[str, str]] = {}
    for path in manifest_paths:
        source_path = source / path
        if not source_path.is_file() or source_path.is_symlink():
            _fail("manifest_paths", f"missing or unsafe source path: {path}")
        mode = "100755" if source_path.stat().st_mode & 0o111 else "100644"
        blob = _sha(_git(source, "hash-object", "--", path).strip(), "manifest_paths")
        source_records[path] = (mode, blob)
    candidate_map = {path: (mode, blob) for path, mode, blob in candidate_records}
    for path, expected in source_records.items():
        if candidate_map.get(path) != expected:
            _fail("manifest_paths", f"candidate mode/blob differs: {path}")

    preserved_before = _preserved(_json(evidence / "preserved-before.json", "preserved_before"), "preserved_before")
    preserved_after = _preserved(_json(evidence / "preserved-after.json", "preserved_after"), "preserved_after")
    target_tree = _lines(evidence / "target-tree.txt", "target_tree")
    if target_tree != [record[0] for record in candidate_records]:
        _fail("target_tree", "evidence differs from committed candidate")

    base_records = _git_base_objects(candidate, base_sha)
    for path, blob in preserved_before.items():
        if base_records.get(path) != blob or candidate_map.get(path, (None, None))[1] != blob:
            _fail("preserved_after", f"preserved base/final blob differs: {path}")
    removed_paths = _lines(evidence / "removed-paths.txt", "removed_paths")
    expected_removed = sorted(set(base_records) - set(preserved_before))
    if removed_paths != expected_removed:
        _fail("removed_paths", "does not exactly describe removed base paths")

    audit = _json(public_audit_file, "public_audit_ok")
    public_audit_ok = (
        isinstance(audit, dict)
        and audit.get("clean") is True
        and isinstance(audit.get("findings"), dict)
        and not audit["findings"]
    )

    snapshot: dict[str, object] = {
        "commit_count": commit_count,
        "base_sha": base_sha,
        "local_sha": local_sha,
        "github_main_sha": branch_sha,
        "parent_shas": parent_shas,
        "candidate_tree_sha256": canonical_tree_digest(candidate_records),
        "anonymous_tree_sha256": canonical_tree_digest(anonymous_records),
        "github_tree_sha256": canonical_tree_digest(github_records),
        "manifest_paths": manifest_paths,
        "candidate_paths": [record[0] for record in candidate_records],
        "anonymous_paths": [record[0] for record in anonymous_records],
        "github_paths": [record[0] for record in github_records],
        "preserved_before": preserved_before,
        "preserved_after": preserved_after,
        "public_audit_ok": public_audit_ok,
        "github_tree_complete": True,
        "github_tree_entry_count": github_entry_count,
        "fetch_urls": [*_remote_urls(candidate, False), *_remote_urls(anonymous, False)],
        "push_urls": [*_remote_urls(candidate, True), *_remote_urls(anonymous, True)],
        "removed_path_count": len(removed_paths),
        "manifest_count": len(manifest_paths),
        "preserved_blob_count": len(preserved_before),
        "audit_finding_count": 0 if public_audit_ok else None,
    }
    return verify_snapshot(snapshot)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--candidate-evidence", required=True, type=Path)
    parser.add_argument("--anonymous", required=True, type=Path)
    parser.add_argument("--github-main", required=True, type=Path)
    parser.add_argument("--github-tree", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--public-audit", type=Path, default=None)
    args = parser.parse_args(argv)
    manifest = args.manifest or args.source / "release" / "software-files.txt"
    public_audit = args.public_audit or args.anonymous.parent / f"{args.anonymous.name}-audit.json"
    try:
        result = build_snapshot(
            args.candidate, args.candidate_evidence, args.anonymous, args.source,
            manifest, args.github_main, args.github_tree, public_audit,
        )
    except (OSError, subprocess.CalledProcessError, VerificationError) as exc:
        print(f"verify_public_repo: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"verify_public_repo: verified {result['github_main_sha']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
