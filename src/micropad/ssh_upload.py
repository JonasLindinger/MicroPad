# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Strict-host-key SSH/SFTP deployment with secret-free operator download script."""

from __future__ import annotations

import os
import posixpath
import secrets
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from micropad.constants import AI_NOTICE
from micropad.models import Settings


class SSHUploadError(RuntimeError):
    """Raised when SSH configuration is invalid or a deployment step fails."""


@dataclass(frozen=True)
class CompletedCommand:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def run(self, argv: list[str], input_text: str | None = None) -> CompletedCommand:
        pass


class SubprocessRunner:
    def run(self, argv: list[str], input_text: str | None = None) -> CompletedCommand:
        # S603: argv is assembled in-code as an explicit immutable list of arguments
        # (never a shell string and never user-controlled text), so this is safe.
        result = subprocess.run(  # noqa: S603
            argv, input=input_text, text=True, capture_output=True, check=False, shell=False
        )
        return CompletedCommand(result.returncode, result.stdout, result.stderr)


_PLACEHOLDER_TOKENS = (
    "changeme",
    "change-me",
    "change_me",
    "replaceme",
    "replace-me",
    "replace_me",
    "your_key",
    "your-key",
    "yourkey",
    "your_host",
    "your-host",
    "your_hostname",
    "your_user",
    "your-user",
    "your_username",
    "put_key_here",
    "put_your",
)


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    if "<" in lowered or ">" in lowered:
        return True
    return any(token in lowered for token in _PLACEHOLDER_TOKENS)


def _validate(settings: Settings) -> None:
    if not settings.ssh_host or not settings.ssh_user or not settings.ssh_key:
        raise SSHUploadError("ssh_host, ssh_user, and ssh_key are required")
    if not (1 <= settings.ssh_port <= 65535):
        raise SSHUploadError("ssh_port must be within the range 1-65535")
    for field_name, value in (
        ("ssh_host", settings.ssh_host),
        ("ssh_user", settings.ssh_user),
        ("ssh_key", settings.ssh_key),
        ("ssh_known_hosts", settings.ssh_known_hosts),
        ("remote_path", settings.remote_path),
    ):
        if _looks_like_placeholder(value):
            raise SSHUploadError(
                f"{field_name} looks like an unfilled placeholder; supply a real value"
            )
    if not posixpath.isabs(settings.remote_path):
        raise SSHUploadError("remote_path must be an absolute path")
    if settings.ssh_validate_command.count("{temp_path}") != 1:
        raise SSHUploadError("ssh_validate_command must contain {temp_path} exactly once")


class SSHDeployer:
    """Upload generated automation to a remote host via sftp, then validate and atomically replace it."""

    def __init__(self, settings: Settings, runner: CommandRunner | None = None) -> None:
        _validate(settings)
        self.settings = settings
        self.runner = runner or SubprocessRunner()

    def _common_options(self) -> list[str]:
        return [
            "-i",
            os.path.expanduser(self.settings.ssh_key),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={os.path.expanduser(self.settings.ssh_known_hosts)}",
        ]

    def _destination(self) -> str:
        return f"{self.settings.ssh_user}@{self.settings.ssh_host}"

    def _sftp_argv(self) -> list[str]:
        return [
            "sftp",
            "-P",
            str(self.settings.ssh_port),
            *self._common_options(),
            self._destination(),
        ]

    def _ssh_argv(self, command: str) -> list[str]:
        return [
            "ssh",
            "-p",
            str(self.settings.ssh_port),
            *self._common_options(),
            self._destination(),
            command,
        ]

    def _run_checked(self, argv: list[str], input_text: str | None, operation: str) -> None:
        result = self.runner.run(argv, input_text)
        if result.returncode != 0:
            raise SSHUploadError(f"SSH {operation} failed with exit code {result.returncode}")

    def deploy(self, yaml_text: str) -> str:
        suffix = secrets.token_hex(8)
        remote_dir = posixpath.dirname(self.settings.remote_path)
        remote_temp = posixpath.join(
            remote_dir, f".{posixpath.basename(self.settings.remote_path)}.{suffix}"
        )
        local_path: Path | None = None
        uploaded = False
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", suffix=".yaml", delete=False
            ) as handle:
                handle.write(yaml_text)
                handle.flush()
                os.fsync(handle.fileno())
                local_path = Path(handle.name)
            batch = f"put {shlex.quote(str(local_path))} {shlex.quote(remote_temp)}\n"
            self._run_checked(self._sftp_argv(), batch, "upload")
            uploaded = True
            validate = self.settings.ssh_validate_command.format(temp_path=shlex.quote(remote_temp))
            self._run_checked(self._ssh_argv(validate), None, "validation")
            move = f"mv -f -- {shlex.quote(remote_temp)} {shlex.quote(self.settings.remote_path)}"
            self._run_checked(self._ssh_argv(move), None, "atomic replacement")
            uploaded = False
            self._run_checked(self._ssh_argv(self.settings.ssh_reload_command), None, "reload")
            return remote_temp
        finally:
            if uploaded:
                self.runner.run(self._ssh_argv(f"rm -f -- {shlex.quote(remote_temp)}"))
            if local_path is not None:
                local_path.unlink(missing_ok=True)


def downloadable_ssh_script(settings: Settings) -> str:
    host = shlex.quote(settings.ssh_host)
    user = shlex.quote(settings.ssh_user)
    remote = shlex.quote(settings.remote_path)
    known_hosts = shlex.quote(os.path.expanduser(settings.ssh_known_hosts))
    return f"""#!/usr/bin/env bash
# {AI_NOTICE}
set -euo pipefail
: "${{SSH_KEY:?Set SSH_KEY}}"
: "${{AUTOMATION_YAML:?Set AUTOMATION_YAML}}"
remote_temp={remote}."$(openssl rand -hex 8)"
cleanup() {{ ssh -p {settings.ssh_port} -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile={known_hosts} {user}@{host} "rm -f -- '$remote_temp'" || true; }}
trap cleanup EXIT
printf 'put %q %q\\n' "$AUTOMATION_YAML" "$remote_temp" | sftp -P {settings.ssh_port} -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile={known_hosts} {user}@{host}
ssh -p {settings.ssh_port} -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile={known_hosts} {user}@{host} "test -s '$remote_temp' && mv -f -- '$remote_temp' {remote} && {settings.ssh_reload_command}"
trap - EXIT
"""
