# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Configuration history: what changed, and how to go back (B7).

Every save of the configuration leaves a snapshot beside it, so a bad edit is a
restore rather than a re-typing session. Three rules keep that useful instead of
noisy:

* **Deduplicated** — an unchanged payload is not recorded (the UI autosaves on every
  edit, and identical consecutive snapshots would bury the interesting ones).
* **Bounded** — only the newest ``SNAPSHOT_LIMIT`` snapshots are kept, so the
  directory cannot grow without end.
* **Addressed by opaque id** — ids are generated (timestamp + content hash) and
  validated with an exact pattern before any file is opened, so a snapshot id from
  an HTTP request can never escape the history directory; revisions are listed newest
  first by write time, with the id as the tiebreaker inside the same second.

Snapshots hold the same data as the configuration file itself (including the Home
Assistant token and the SSH key), so they live next to it with the same file mode and
must never be committed: see the ignore rules and the note in docs/security-release.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: How many snapshots are kept (newest first); older ones are pruned on write.
SNAPSHOT_LIMIT = 20

#: Generated id shape: UTC timestamp + short content hash, e.g.
#: 20260915T093012Z-1a2b3c4d. Validated before use, never trusted as a path part.
SNAPSHOT_ID_PATTERN = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")

_SUFFIX = ".history"


@dataclass(frozen=True)
class Snapshot:
    """One recorded configuration revision."""

    snapshot_id: str
    created_at: str
    pages: int
    items: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.snapshot_id,
            "created_at": self.created_at,
            "pages": self.pages,
            "items": self.items,
        }


def history_dir(config_path: Path) -> Path:
    """Directory holding the snapshots for one configuration file."""
    return config_path.with_name(config_path.name + _SUFFIX)


def _id_for(payload: dict[str, Any], now: datetime) -> str:
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{_content_digest(payload)}"


def _as_list(value: Any) -> list[Any]:
    """A JSON field that should be a list, or an empty list."""
    return value if isinstance(value, list) else []


def _as_map(value: Any) -> dict[str, Any]:
    """A JSON field that should be an object, or an empty object."""
    return value if isinstance(value, dict) else {}


def _content_digest(payload: dict[str, Any]) -> str:
    """Content address of a configuration document (ignores the snapshot stamp)."""
    comparable = {key: value for key, value in payload.items() if key != "_snapshot_created_at"}
    return hashlib.sha256(
        json.dumps(comparable, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:8]


def _summarize(payload: dict[str, Any]) -> tuple[int, int]:
    pages = payload.get("pages")
    if not isinstance(pages, list):
        return 0, 0
    items = 0
    for page in pages:
        if isinstance(page, dict) and isinstance(page.get("items"), list):
            items += len(page["items"])
    return len(pages), items


def list_snapshots(config_path: Path) -> list[Snapshot]:
    """Recorded snapshots, newest first. Unreadable files are skipped, not fatal."""
    directory = history_dir(config_path)
    if not directory.is_dir():
        return []
    snapshots: list[Snapshot] = []
    for path in sorted(directory.glob("*.json"), reverse=True):
        if not SNAPSHOT_ID_PATTERN.fullmatch(path.stem):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        pages, items = _summarize(payload)
        created_at = str(payload.get("_snapshot_created_at", ""))
        snapshots.append(Snapshot(path.stem, created_at, pages, items))
    # Newest first by write time: the recorded stamp has one-second resolution, so
    # several edits inside the same second (the UI autosaves on every change) would
    # otherwise come back in an arbitrary order. The id breaks exact ties.
    def written_at(path: Path) -> tuple[int, str]:
        try:
            return (path.stat().st_mtime_ns, path.stem)
        except OSError:
            return (0, path.stem)

    snapshots.sort(key=lambda snapshot: written_at(directory / f"{snapshot.snapshot_id}.json"),
                   reverse=True)
    return snapshots


def load_snapshot(config_path: Path, snapshot_id: str) -> dict[str, Any]:
    """Read one snapshot by id.

    Raises ``ValueError`` for an id that is not a generated one, and
    ``FileNotFoundError`` when it is not there — the caller turns both into a
    structured error rather than a traceback.
    """
    if not SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id):
        raise ValueError("unknown snapshot id")
    path = history_dir(config_path) / f"{snapshot_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("snapshot is not a configuration object")
    payload.pop("_snapshot_created_at", None)
    return payload


def record(config_path: Path, payload: dict[str, Any]) -> str | None:
    """Record a snapshot of ``payload``; returns its id, or None when unchanged.

    Writes atomically (temp file + rename) so a crash mid-write cannot leave a
    half-written snapshot that a later restore would treat as real.
    """
    directory = history_dir(config_path)
    # Content-addressed dedupe: the id already carries the payload hash, so an
    # unchanged configuration is recognised regardless of how many saves land in the
    # same second (the UI autosaves on every edit). A configuration that was saved
    # before and is restored back to that state is therefore not recorded twice.
    digest = _content_digest(payload)
    if any(snapshot.snapshot_id.endswith(f"-{digest}") for snapshot in list_snapshots(config_path)):
        return None

    now = datetime.now(timezone.utc)
    snapshot_id = _id_for(payload, now)
    directory.mkdir(parents=True, exist_ok=True)
    stored = dict(payload)
    stored["_snapshot_created_at"] = now.isoformat(timespec="seconds")
    _write_atomic(directory / f"{snapshot_id}.json", stored)
    _prune(directory)
    return snapshot_id


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
        os.chmod(temporary, 0o600)  # same sensitivity as the configuration itself
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _prune(directory: Path) -> None:
    snapshots = sorted(
        (path for path in directory.glob("*.json") if SNAPSHOT_ID_PATTERN.fullmatch(path.stem)),
        reverse=True,
    )
    for stale in snapshots[SNAPSHOT_LIMIT:]:
        stale.unlink(missing_ok=True)


def diff_configs(older: dict[str, Any], newer: dict[str, Any]) -> list[str]:
    """Human-readable summary of what changed between two configuration documents.

    Deliberately structural rather than a text diff: the operator wants to know which
    page or key changed, not which byte of JSON moved.
    """
    lines: list[str] = []

    def pages_of(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for page in _as_list(document.get("pages")):
            if isinstance(page, dict) and page.get("page_id"):
                found[str(page["page_id"])] = page
        return found

    old_pages, new_pages = pages_of(older), pages_of(newer)
    for page_id in sorted(set(new_pages) - set(old_pages)):
        lines.append(f'page "{page_id}" added ({len(new_pages[page_id].get("items") or [])} items)')
    for page_id in sorted(set(old_pages) - set(new_pages)):
        lines.append(f'page "{page_id}" removed')
    for page_id in sorted(set(old_pages) & set(new_pages)):
        old_page, new_page = old_pages[page_id], new_pages[page_id]
        if old_page.get("title") != new_page.get("title"):
            lines.append(
                f'page "{page_id}" title: {old_page.get("title")!r} -> {new_page.get("title")!r}'
            )
        if old_page.get("parent") != new_page.get("parent"):
            lines.append(
                f'page "{page_id}" parent: {old_page.get("parent")!r} -> {new_page.get("parent")!r}'
            )
        old_items = _as_list(old_page.get("items"))
        new_items = _as_list(new_page.get("items"))
        if len(old_items) != len(new_items):
            lines.append(f'page "{page_id}" items: {len(old_items)} -> {len(new_items)}')
        for index, (before, after) in enumerate(zip(old_items, new_items, strict=False)):
            if before == after:
                continue
            if not isinstance(before, dict) or not isinstance(after, dict):
                lines.append(f'page "{page_id}" item #{index}: replaced')
                continue
            name = after.get("name") or before.get("name") or f"#{index}"
            changed = sorted(
                key for key in set(before) | set(after) if before.get(key) != after.get(key)
            )
            lines.append(f'page "{page_id}" item "{name}": {", ".join(changed)}')

    old_keys = _as_map(older.get("global_keymap"))
    new_keys = _as_map(newer.get("global_keymap"))
    for key_id in sorted(set(old_keys) | set(new_keys)):
        if old_keys.get(key_id) != new_keys.get(key_id):
            lines.append(
                f"global keymap {key_id}: {_describe_binding(old_keys.get(key_id))} -> "
                f"{_describe_binding(new_keys.get(key_id))}"
            )

    old_settings = _as_map(older.get("settings"))
    new_settings = _as_map(newer.get("settings"))
    for field in sorted(set(old_settings) | set(new_settings)):
        if field in {"ha_token", "ssh_key"}:
            # Compared, never printed: the diff is shown in the browser.
            if old_settings.get(field) != new_settings.get(field):
                lines.append(f"{field}: changed")
            continue
        if old_settings.get(field) != new_settings.get(field):
            lines.append(
                f"setting {field}: {old_settings.get(field)!r} -> {new_settings.get(field)!r}"
            )

    return lines or ["no differences"]


def _describe_binding(binding: Any) -> str:
    if not isinstance(binding, dict):
        return "unset"
    action = binding.get("action") or "none"
    entity = binding.get("entity") or ""
    target = binding.get("target_page") or ""
    detail = entity or target
    return f"{action}({detail})" if detail else str(action)
