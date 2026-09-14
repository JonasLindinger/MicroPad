#!/usr/bin/env python3
# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Public-release hygiene audit: reject secrets, placeholders and personal data.

Scans a candidate public tree (``--tree``; default: every tracked repo file via
``git ls-files``, honouring ``.gitignore``) for a candidate about to be released
publicly. It is conservative and precise: it flags only real secret shapes,
non-approved placeholders, and personal data, and fails closed (non-zero exit,
no ``--output`` written) on any finding.

Detectors
---------
* **file shape**: the auditor never scans repository metadata (``.git``) or the
  ignored/worktree offender directories; it rejects secret-located filenames
  (``config.json``, ``.env``, ``id_rsa``, ``id_ed25519``, ``known_hosts``) and
  backup/derived suffixes (``.bak``, ``.backup``, ``.orig``, ``.pem``, ``.key``,
  ``.pyc``).
* **content**: JWT/bearer and provider-prefixed keys (GitHub, AWS, Google,
  Stripe, Slack, Google-OAuth), generic high-entropy tokens in a credential
  context, private-key PEM headers, credential-bearing URLs, absolute
  ``/home/<person>`` paths, non-placeholder ``password`` assignments, private /
  non-approved dotted IPv4, and personal data (derived identity/HA terms, real
  emails, phone numbers, MAC addresses).

Every secret-looking value is accepted only if it is an APPROVED placeholder
from ``release/allowed-public-values.json`` (broker ``192.168.0.100``, user
``micropad``, password ``replace-me``, and the synthetic test-only tokens). Any
other secret-shaped value is a finding. Detector literals and test canaries are
built from adjacent fragments so this module carries no contiguous value that a
later scan would itself have to reject.

Exit 0 and a JSON report (counts + SHA-256 of matched values, never the values
or source lines) are produced only when the candidate is clean.

Usage:
    scripts/audit_public_release.py --tree build/public-tree \
        --private-config config.json --output build/public-audit.json
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import platform
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

# Files we must never ship because they hold generated/private state or derived
# secrets. Suffix rejection is deliberately broad to kill backup/export cruft.
_REJECT_FILENAMES = frozenset({"config.json", ".env", "id_rsa", "id_ed25519", "known_hosts"})
_REJECT_FILENAME_LOWER = {name.lower() for name in _REJECT_FILENAMES}
_REJECT_SUFFIXES = frozenset({".bak", ".backup", ".orig", ".pem", ".key", ".pyc"})
# Directories whose entire contents are internal/build state (never scanned).
_REJECT_DIRS = frozenset(
    {
        ".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules",
        "playwright-report", "test-results", "build", ".arduino",
    }
)

# --- secret shapes ---------------------------------------------------------
# JWT/bearer tokens. A standard JWT always opens with the base64url JSON header
# marker ``eyJ``. (A generic three-segment dotted match is *not* used because it
# falsely matches ordinary hostnames such as ``registry.example.org``.)
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]{6,}(?:\.[A-Za-z0-9_\-]+){1,2}")

# Provider-specific credential formats.
_PROVIDER_RES = [
    re.compile(r"(?i)ghp_[A-Za-z0-9]{20,}"),            # GitHub classic token
    re.compile(r"(?i)github_pat_[A-Za-z0-9_]{20,}"),    # GitHub fine-grained token
    re.compile(r"(?i)glpat-[A-Za-z0-9_\-]{16,}"),       # GitLab token
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                # AWS access key id
    re.compile(r"(?i)AIza[0-9A-Za-z_\-]{30,}"),         # Google API key
    re.compile(r"(?i)sk_live_[0-9A-Za-z]{16,}"),        # Stripe secret key
    re.compile(r"(?i)xox[baprs]-[0-9A-Za-z\-]{10,}"),   # Slack token
    re.compile(r"(?i)(?:ya29|ya30)\.[0-9A-Za-z_\-]+"),  # Google OAuth token
]

# Generic high-entropy token only when it sits in a credential context and is
# genuinely high-entropy (length >= 24 with upper + lower + digit). This avoids
# flagging git SHAs (single-case), the firmware base64 alphabet (no digit, no
# credential context), and low-entropy test sentinels (fake-token, secret-token).
_HIGH_ENTROPY_RE = re.compile(r"[A-Za-z0-9+/=_-]{24,}")
_CRED_CONTEXT_RE = re.compile(
    r"(?i)\b(bearer|token|secret|key|password|pass|auth|api[_-]?key|credential)\b"
)

_PRIVATE_KEY_RE = re.compile(
    r"(?i)-----BEGIN (?:RSA |DSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"
)

# scheme:// URL that embeds a user:pass credential before the host.
_CRED_URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^/\s:@]+:[^/\s@]+@")

# Service/role accounts are not personal data even when written "name@domain".
_SERVICE_LOCAL_PARTS = frozenset({
    "git", "deploy", "root", "admin", "user", "nobody", "www", "info",
    "support", "postmaster", "test", "example", "mail", "service", "host",
    "noreply", "no-reply", "admin1",
})

# Password assignments against the approved placeholder. Keyed on the
# password-word only, excluding the bare word ``pass`` (a verb, e.g. "no pass:")
# and bare ``token``/``secret``/``key`` (hundreds of synthetic test sentinels
# belong to the token detector instead). The literal may be quoted or bare and
# must be a plain string value, not an expression or interpolation.
_PASSWORD_ASSIGN_RE = re.compile(
    r"(?i)\b(password|passwd|wifi_pass|mqtt_pass|wifi_password|mqtt_password)\b"
    r"[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9._\-]+)[\"']?(?=$|\s|[,;:)}\]])"
)

# Absolute /home/<person>/ paths revealing a real account name.
_HOME_PATH_RE = re.compile(r"/(?:home|Users)/([^\s/]{1,64})(?:/|$)")

# Dotted IPv4 values.
_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")

# Personal-data shapes for a release that must not expose a person/identity.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_MAC_RE = re.compile(r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}")
# Telephone candidates use structural grouping rather than a broad digit/separator
# span. Domestic numbers require a three-digit area code (optionally parenthesized)
# plus seven subscriber digits. The final 3+4 subscriber groups may be separated
# or contiguous, and an optional ``+`` country code may precede the area code.
# A separate candidate covers compact international numbers. These shapes retain
# mixed separators without matching ordinary KiCad decimal coordinate pairs such
# as ``106.295 116`` or ``106.295 116.98``.
_PHONE_RES = (
    re.compile(
        r"(?<![A-Za-z0-9_.-])(?:\+[1-9][0-9]{0,2}[-. /]*)?"
        r"(?:\([1-9][0-9]{2}\)|[1-9][0-9]{2})[-. /]+"
        r"[0-9]{3}[-. /]*[0-9]{4}(?![A-Za-z0-9_.-])"
    ),
    re.compile(r"(?<![A-Za-z0-9_.-])\+[1-9][0-9]{6,14}(?![A-Za-z0-9_.-])"),
)
# Reserved/benign second-level+ TLDs: never personal even if it contains '@'.
_BENIGN_DOMAINS = ("local", "example", "test", "invalid", "localhost")


def _is_high_entropy(value: str) -> bool:
    if len(value) < 24:
        return False
    return (
        any(c.islower() for c in value)
        and any(c.isupper() for c in value)
        and any(c.isdigit() for c in value)
    )


def _credential_context(line: str) -> bool:
    return bool(_CRED_CONTEXT_RE.search(line))


def _token_candidates(line: str) -> Iterable[str]:
    for match in _JWT_RE.finditer(line):
        yield match.group(0)
    for rx in _PROVIDER_RES:
        for match in rx.finditer(line):
            yield match.group(0)
    if _credential_context(line):
        for match in _HIGH_ENTROPY_RE.finditer(line):
            token = match.group(0)
            if _is_high_entropy(token):
                yield token


class _LoadError(RuntimeError):
    """Raised when the approved-values allowlist cannot be parsed."""


def load_allowlist(path: Path | None = None) -> dict:
    """Load ``release/allowed-public-values.json`` from the repo root by default."""
    source = path or (_REPO / "release" / "allowed-public-values.json")
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise _LoadError(f"allowlist not found: {source}") from None
    except json.JSONDecodeError as exc:  # pragma: no cover - defense in depth
        raise _LoadError(f"allowlist is not valid JSON: {source}: {exc}") from None

    def _values(key: str) -> set[str]:
        entry = data.get(key, {})
        if isinstance(entry, dict):
            value = entry.get("values", [])
        else:
            value = entry
        return {str(v) for v in value} if isinstance(value, list) else set()

    return {
        "allowed_ips": _values("allowed_ips"),
        "allowed_users": _values("allowed_users"),
        "allowed_password_placeholders": _values("allowed_password_placeholders"),
        "allowed_tokens": _values("allowed_tokens"),
        "allowed_repo_identifiers": _values("allowed_repo_identifiers"),
        "benign_email_tlds": _values("benign_email_tlds") or _BENIGN_DOMAINS,
        "ip_documentation_ranges": _values("ip_documentation_ranges"),
    }


@dataclass(frozen=True)
class Finding:
    """One forbidden match: class, offset, and a digest, never the matched text."""

    code: str
    path: str
    line: int
    value_sha256: str  # SHA-256 of the matched value; the value itself is not stored.


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _is_approved_ip(ip: str, allowed: dict) -> bool:
    if ip in allowed["allowed_ips"]:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for cidr in allowed["ip_documentation_ranges"]:
        try:
            network = ipaddress.ip_network(cidr)
        except ValueError:
            continue
        if addr in network:
            return True
    return False


def _email_domain_benign(email: str, allowed: dict) -> bool:
    domain = email.rsplit("@", 1)[-1].lower()
    tld = domain.rsplit(".", 1)[-1] if "." in domain else domain
    benign = {str(t).lower() for t in allowed.get("benign_email_tlds", _BENIGN_DOMAINS)}
    if domain in benign or tld in benign:
        return True
    local = email.rsplit("@", 1)[0].lower()
    # A service/role account (git@github.com, deploy@host) is not a person.
    if local in _SERVICE_LOCAL_PARTS:
        return True
    return False


def _findings_for_text(path_str: str, lines: Sequence[str], allowed: dict, forbidden: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    append = findings.append

    for lineno, line in enumerate(lines, start=1):

        # 1) secret shapes -----------------------------------------------------
        for token in _token_candidates(line):
            if token not in allowed["allowed_tokens"]:
                append(Finding("token", path_str, lineno, _sha(token)))

        m = _PRIVATE_KEY_RE.search(line)
        if m:
            append(Finding("private-key", path_str, lineno, _sha(m.group(0))))

        m = _CRED_URL_RE.search(line)
        if m:
            append(Finding("credential-url", path_str, lineno, _sha(m.group(0))))

        # 2) password assignments against the approved placeholders -----------
        m = _PASSWORD_ASSIGN_RE.search(line)
        allowed_password = allowed["allowed_password_placeholders"] | allowed["allowed_tokens"]
        if m and m.group(2) not in allowed_password:
            append(Finding("password", path_str, lineno, _sha(m.group(2))))

        # 3) dotted IPv4 outside the allowlist --------------------------------
        for match in _IPV4_RE.finditer(line):
            ip = match.group(0)
            if not _is_approved_ip(ip, allowed):
                append(Finding("ip-address", path_str, lineno, _sha(ip)))

        # 4) personal-data -----------------------------------------------------
        email = _EMAIL_RE.search(line)
        if email and not _email_domain_benign(email.group(0), allowed):
            append(Finding("personal-data", path_str, lineno, _sha(email.group(0))))

        mac = _MAC_RE.search(line)
        if mac:
            append(Finding("personal-data", path_str, lineno, _sha(mac.group(0))))

        phone = next((match for rx in _PHONE_RES if (match := rx.search(line))), None)
        if phone:
            append(Finding("personal-data", path_str, lineno, _sha(phone.group(0))))

        # Derived identity / HA terms matched case-insensitively.
        lowered = line.lower()
        for term in forbidden:
            if term and term in lowered:
                append(Finding("personal-data", path_str, lineno, _sha(term)))

        # 5) absolute /home/<person> paths (person account name gated on terms)
        m = _HOME_PATH_RE.search(line)
        if m and m.group(1).lower() in forbidden:
            append(Finding("private-path", path_str, lineno, _sha(m.group(1))))

    return findings


def _scan_path(root: Path, relative: Path, allowed: dict, forbidden: set[str], reject_dirs: frozenset) -> list[Finding]:
    full = root / relative
    if not full.is_file():
        return []
    name = full.name
    name_lower = name.lower()
    findings: list[Finding] = []
    rel_str = (relative or Path(name)).as_posix()

    if name_lower in _REJECT_FILENAME_LOWER:
        code = "generated-state" if name_lower == "config.json" else "backup"
        findings.append(Finding(code, rel_str, 0, _sha(name)))
        # config.json is definitively generated runtime state; its presence is
        # the finding and there is nothing further to learn from its contents.
        # Other secret-located names (id_rsa, .env, known_hosts) still have
        # their content scanned so a key inside them is also reported.
        if name_lower == "config.json":
            return findings
    if full.suffix.lower() in _REJECT_SUFFIXES:
        findings.append(Finding("backup", rel_str, 0, _sha(name)))

    try:
        raw = full.read_bytes()
    except OSError:
        return findings
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return findings  # binary assets (bin, images) are not secret-bearing text
    return findings + _findings_for_text(rel_str, text.splitlines(), allowed, forbidden)


def audit_tree(
    root: Path,
    forbidden_terms: Iterable[str] = (),
    allowlist: dict | None = None,
    reject_dirs: Iterable[str] = _REJECT_DIRS,
) -> list[Finding]:
    """Detect secret/placeholder/personal-data matches under ``root``.

    Returns a (deduplicated, order-stable) list of :class:`Finding`. It never
    fails on allowlist absence at this level: the caller (CLI) reports and
    aborts; direct test callers pass an explicit allowlist built from the repo,
    so the gate genuinely scans.
    """
    allowed = load_allowlist() if allowlist is None else allowlist
    forbidden = {t.strip().lower() for t in forbidden_terms if t and t.strip()}
    rejected = frozenset(reject_dirs)

    findings: list[Finding] = []
    seen: set[tuple[str, int, str]] = set()

    def _walk(directory: Path, prefix: Path) -> None:
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for entry in entries:
            rel = (prefix / entry.name) if entry.name else prefix
            if entry.is_dir():
                if entry.name not in rejected:
                    _walk(entry, rel)
            else:
                for finding in _scan_path(root, rel, allowed, forbidden, rejected):
                    key = (finding.code, finding.line, finding.path)
                    if key not in seen:
                        seen.add(key)
                        findings.append(finding)

    _walk(root, Path())
    return findings


def _git_value(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(_REPO), *args], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""
    return out


def derive_forbidden_terms(private_config: str | None = None, include_git: bool = True) -> set[str]:
    """Personal identifiers derived from the operator's environment and config.

    Includes git identity (``user.name``, ``user.email``), the machine
    hostname, and non-empty string values from the ignored private
    ``config.json`` under its personalised keys. Absent git identity and an
    absent ``config.json`` yield an empty set, which is exactly right for a
    clean-room build that must not couple audit cleanliness to a person.
    """
    terms: set[str] = set()
    if include_git:
        for key in ("user.name", "user.email"):
            value = _git_value("config", "--get", key).strip()
            if value:
                terms.add(value)
    try:
        hostname = platform.node().strip()
        if hostname:
            terms.add(hostname)
    except OSError:
        pass
    if private_config and Path(private_config).is_file():
        try:
            cfg = json.loads(Path(private_config).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = {}
        keys = ("entity", "name", "friendly_name", "ha_url", "mqtt_host", "mqtt_user",
                "ssh_host", "ssh_user", "ssh_key", "remote_path")

        def _collect(node: object) -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in keys and isinstance(v, str) and v.strip():
                        terms.add(v.strip())
                    else:
                        _collect(v)
            elif isinstance(node, list):
                for item in node:
                    _collect(item)

        _collect(cfg)
    return {t for t in terms if t}


def _write_report(path: Path, findings: list[Finding], scanned: int) -> None:
    counts: dict[str, int] = {}
    digests: dict[str, list[str]] = {}
    for finding in findings:
        counts[finding.code] = counts.get(finding.code, 0) + 1
        digests.setdefault(finding.code, []).append(finding.value_sha256)
    report = {
        "clean": not findings,
        "files_scanned": scanned,
        "findings": counts,
    }
    if digests:
        report["matched_value_sha256"] = {code: sorted(set(hash_list)) for code, hash_list in digests.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _tracked_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(_REPO), "ls-files"], capture_output=True, text=True, check=True
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    return [line for line in out.splitlines() if line]


def _auditable_file_count(root: Path, reject_dirs: frozenset = _REJECT_DIRS) -> int:
    """Count candidate files reached by the same directory exclusions as audit_tree."""
    count = 0
    for _directory, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in reject_dirs]
        count += len(files)
    return count


def _verify_readable(root: Path, reject_dirs: frozenset = _REJECT_DIRS) -> None:
    """Fail-closed traversal/readability probe (P1.18).

    Raises ``OSError`` on any directory that cannot be entered or any candidate
    file that cannot be opened, so an unreadable tree can never pass silently.
    """
    def _raise(error: OSError) -> None:
        raise error

    for directory, dirs, files in os.walk(root, onerror=_raise):
        dirs[:] = [name for name in dirs if name not in reject_dirs]
        for name in files:
            with (Path(directory) / name).open("rb"):
                pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tree", type=Path, default=None,
                        help="candidate tree to scan (default: tracked files at repo root)")
    parser.add_argument("--private-config", type=str, default=None,
                        help="optional git-ignored config.json to derive personal terms from")
    parser.add_argument("--output", type=Path, default=None,
                        help="write build/public-audit.json (clean pass only)")
    args = parser.parse_args(argv)

    try:
        allowed = load_allowlist()
    except _LoadError as exc:
        print(f"audit_public_release: {exc}", file=sys.stderr)
        return 2
    forbidden = derive_forbidden_terms(args.private_config)

    if args.tree is not None:
        root = args.tree
        if not root.exists() or not root.is_dir():
            print(
                f"audit_public_release: tree not found or not a directory: {root}",
                file=sys.stderr,
            )
            return 3
        try:
            _verify_readable(root)
        except OSError as exc:
            print(f"audit_public_release: cannot scan tree: {exc}", file=sys.stderr)
            return 4
        findings = audit_tree(root, forbidden_terms=forbidden, allowlist=allowed)
        scanned = _auditable_file_count(root)
    else:
        root = _REPO
        tracked_set = set(_tracked_files())
        findings = []
        seen: set[tuple[str, int, str]] = set()
        for relative in sorted(tracked_set):
            if any(part in _REJECT_DIRS for part in Path(relative).parts):
                continue
            for finding in _scan_path(root, Path(relative), allowed, forbidden, frozenset()):
                key = (finding.code, finding.line, finding.path)
                if key not in seen:
                    seen.add(key)
                    findings.append(finding)
        scanned = len(tracked_set)

    if scanned == 0:
        print("audit_public_release: no files scanned", file=sys.stderr)
        return 5

    if findings:
        print(f"audit_public_release: FAILED with {len(findings)} forbidden finding(s)", file=sys.stderr)
        for finding in findings:
            print(f"  {finding.code}:{finding.path}:{finding.line}", file=sys.stderr)
        return 1

    if args.output:
        _write_report(args.output, [], scanned)
    print(f"audit_public_release: clean ({scanned} files scanned)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())