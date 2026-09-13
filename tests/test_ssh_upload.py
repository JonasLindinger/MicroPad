# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Tests for strict-host-key SSH/SFTP deployment and the secret-free deploy script."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass

import pytest

from micropad.constants import AI_NOTICE
from micropad.models import Settings
from micropad.ssh_upload import (
    CompletedCommand,
    SSHDeployer,
    SSHUploadError,
    downloadable_ssh_script,
)


@dataclass(frozen=True)
class RunnerCall:
    argv: list[str]
    input_text: str | None


class RecordingRunner:
    """Capture exact command lines without running any real ssh/sftp/remote command."""

    def __init__(self, failure_index: int | None = None, stderr: str = "") -> None:
        self.failure_index = failure_index
        self.stderr = stderr
        self.calls: list[RunnerCall] = []

    def run(self, argv: list[str], input_text: str | None = None) -> CompletedCommand:
        index = len(self.calls)
        self.calls.append(RunnerCall(list(argv), input_text))
        return CompletedCommand(1 if index == self.failure_index else 0, "", self.stderr)


def valid_settings() -> Settings:
    return Settings(
        ssh_host="ha.local",
        ssh_port=2222,
        ssh_user="deploy",
        ssh_key="/safe/id_ed25519",
        ssh_known_hosts="/safe/known_hosts",
        remote_path="/config/automations/micropad.yaml",
        ssh_validate_command="test -s {temp_path}",
        ssh_reload_command="ha core restart",
    )


def test_deploy_uses_strict_host_key_sftp_temp_and_atomic_move() -> None:
    runner = RecordingRunner()
    settings = valid_settings()
    temporary = SSHDeployer(settings, runner=runner).deploy("id: micropad_controller\n")
    common = [
        "-i",
        "/safe/id_ed25519",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "UserKnownHostsFile=/safe/known_hosts",
    ]
    assert runner.calls[0].argv == ["sftp", "-P", "2222", *common, "deploy@ha.local"]
    assert "put " in runner.calls[0].input_text
    assert temporary.startswith("/config/automations/.micropad.yaml.")
    ssh_base = ["ssh", "-p", "2222", *common, "deploy@ha.local"]
    # Remote commands are delivered as one argv element with shlex-safe quoting; a
    # plain path carries no quotes while a path with special characters is quoted.
    assert runner.calls[1].argv == [*ssh_base, f"test -s {shlex.quote(temporary)}"]
    assert runner.calls[2].argv == [
        *ssh_base,
        f"mv -f -- {shlex.quote(temporary)} {shlex.quote('/config/automations/micropad.yaml')}",
    ]
    assert runner.calls[3].argv == [*ssh_base, "ha core restart"]


def test_sftp_and_ssh_use_explicit_argv_with_no_shell_string() -> None:
    runner = RecordingRunner()
    SSHDeployer(valid_settings(), runner=runner).deploy("id: micropad_controller\n")
    for call in runner.calls:
        assert isinstance(call.argv, list)
        assert all(isinstance(argument, str) for argument in call.argv)
        # The sftp batch is a single put operation supplied as stdin text, never an argv element.
        assert call.input_text is None or call.input_text.startswith("put ")


def test_remote_path_with_spaces_is_shell_quoted_for_the_remote_shell() -> None:
    settings = valid_settings().model_copy(
        update={"remote_path": "/config/dir with space/micropad.yaml"}
    )
    runner = RecordingRunner()
    SSHDeployer(settings, runner=runner).deploy("id: micropad_controller\n")
    move_command = runner.calls[2].argv[-1]
    assert "'/config/dir with space/micropad.yaml'" in move_command
    assert move_command.count("'") >= 4


def test_success_places_reload_last_and_returns_the_remote_temp_path() -> None:
    runner = RecordingRunner()
    returned = SSHDeployer(valid_settings(), runner=runner).deploy("id: micropad_controller\n")
    assert len(runner.calls) == 4
    assert runner.calls[3].argv[-1] == "ha core restart"
    assert returned == shlex.split(runner.calls[2].argv[-1])[3]


def test_missing_connection_fields_rejected_before_any_command() -> None:
    runner = RecordingRunner()
    for field in ("ssh_host", "ssh_user", "ssh_key"):
        settings = valid_settings().model_copy(update={field: ""})
        with pytest.raises(SSHUploadError, match="required"):
            SSHDeployer(settings, runner=runner)
    assert runner.calls == []


def test_non_absolute_remote_path_rejected_before_any_command() -> None:
    settings = valid_settings().model_copy(
        update={"remote_path": "config/automations/micropad.yaml"}
    )
    with pytest.raises(SSHUploadError, match="absolute"):
        SSHDeployer(settings, RecordingRunner())


def test_validate_command_must_contain_temp_path_exactly_once() -> None:
    settings = valid_settings().model_copy(
        update={"ssh_validate_command": "test -f {temp_path} {temp_path}"}
    )
    with pytest.raises(SSHUploadError, match="temp_path"):
        SSHDeployer(settings, RecordingRunner())
    missing = valid_settings().model_copy(update={"ssh_validate_command": "test -f file"})
    with pytest.raises(SSHUploadError, match="temp_path"):
        SSHDeployer(missing, RecordingRunner())


@pytest.mark.parametrize(
    "field", ["ssh_host", "ssh_user", "ssh_key", "remote_path", "ssh_known_hosts"]
)
def test_placeholder_values_rejected_before_any_command(field: str) -> None:
    settings = valid_settings().model_copy(update={field: "your_key_here"})
    with pytest.raises(SSHUploadError, match="placeholder"):
        SSHDeployer(settings, RecordingRunner())


def test_invalid_port_rejected_before_any_command() -> None:
    # model_copy bypasses the pydantic range check, so the deployer's own guard must catch it.
    settings = valid_settings().model_copy(update={"ssh_port": 0})
    with pytest.raises(SSHUploadError, match="port"):
        SSHDeployer(settings, RecordingRunner())


@pytest.mark.parametrize(
    ("failure_index", "operation"),
    [(0, "upload"), (1, "validation"), (2, "atomic replacement"), (3, "reload")],
)
def test_failure_is_redacted_and_cleanup_is_bounded(failure_index: int, operation: str) -> None:
    runner = RecordingRunner(failure_index=failure_index, stderr="private-key-material top-secret")
    with pytest.raises(SSHUploadError, match=operation) as captured:
        SSHDeployer(valid_settings(), runner).deploy("id: micropad_controller\n")
    assert "private-key-material" not in str(captured.value)
    assert "top-secret" not in str(captured.value)
    cleanup = [call for call in runner.calls if "rm -f --" in call.argv[-1]]
    # Remote temp is removed only after SFTP succeeded and while the update is still in flight.
    if failure_index in (1, 2):
        assert cleanup
    else:
        assert not cleanup


def test_validation_failure_prevents_any_rename() -> None:
    runner = RecordingRunner(failure_index=1)
    with pytest.raises(SSHUploadError, match="validation"):
        SSHDeployer(valid_settings(), runner).deploy("id: micropad_controller\n")
    assert all("mv -f --" not in call.argv[-1] for call in runner.calls)


def test_upload_failure_issues_only_the_sftp_call() -> None:
    runner = RecordingRunner(failure_index=0)
    with pytest.raises(SSHUploadError, match="upload"):
        SSHDeployer(valid_settings(), runner).deploy("id: micropad_controller\n")
    assert len(runner.calls) == 1
    assert runner.calls[0].argv[0] == "sftp"


def test_local_temp_file_is_removed_after_failure() -> None:
    runner = RecordingRunner(failure_index=2)
    with pytest.raises(SSHUploadError, match="atomic replacement"):
        SSHDeployer(valid_settings(), runner).deploy("id: micropad_controller\n")
    put_parts = (runner.calls[0].input_text or "").split()
    assert put_parts[0] == "put"
    local_path = put_parts[1]
    assert not os.path.exists(local_path)


def test_download_script_never_contains_configured_key_or_token() -> None:
    settings = Settings(
        ssh_host="ha.local",
        ssh_user="deploy",
        ssh_key="/private/id",
        ha_token="top-" + "secret",
    )
    script = downloadable_ssh_script(settings)
    assert "/private/id" not in script
    assert "top-secret" not in script
    assert "${SSH_KEY:?Set SSH_KEY}" in script


def test_download_script_instructs_operator_to_supply_credentials_and_content() -> None:
    script = downloadable_ssh_script(valid_settings())
    assert "#!/usr/bin/env bash" in script
    assert "set -euo pipefail" in script
    assert "${SSH_KEY:?Set SSH_KEY}" in script
    assert "${AUTOMATION_YAML:?Set AUTOMATION_YAML}" in script
    assert "deploy@ha.local" in script


def test_download_script_carries_the_ai_notice() -> None:
    script = downloadable_ssh_script(valid_settings())
    assert AI_NOTICE in script
