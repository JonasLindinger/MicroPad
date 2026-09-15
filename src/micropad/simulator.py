# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Drive the firmware core from the backend (preview and key-outcome simulation).

``tools/micropad_sim.cpp`` compiles the *shipped* core into a host binary and
answers one question: given this page and this pad state, what does the panel
render and what does a key press do? The configurator's preview uses this instead
of a JavaScript look-alike, so a layout or clipping change in the firmware shows
up on the website without anyone re-implementing it there.

The wire format between here and the binary is the line-oriented directive format
documented in the tool's header comment; it is deliberately simple (no JSON parser
inside the tool, no geometry in the wrapper).
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Overrides the simulator binary location (packaging, CI, custom builds).
SIM_BIN_ENV = "MICROPAD_SIM_BIN"

DEFAULT_TIMEOUT_S = 5.0


class SimulatorUnavailable(RuntimeError):
    """The simulator binary is not built (or not runnable)."""


class SimulatorError(RuntimeError):
    """The simulator ran but rejected the input or printed unusable output."""


def simulator_path() -> Path:
    """Absolute path of the simulator binary (env override wins)."""
    override = os.environ.get(SIM_BIN_ENV)
    if override:
        return Path(override)
    return REPO_ROOT / "build" / "host" / "micropad_sim"


def simulator_available() -> bool:
    """True when the binary exists and is executable."""
    path = simulator_path()
    return path.is_file() and os.access(path, os.X_OK)


def _text(value: Any) -> str:
    """Best-effort string for a possibly-missing draft field."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def page_directives(
    page: Mapping[str, Any],
    *,
    selected: int = 0,
    first_visible: int = 0,
    editing: bool = False,
    network: int = 0,
    usb_host: bool = False,
    portal: Mapping[str, Any] | None = None,
    keys: Sequence[Mapping[str, Any]] = (),
    press: str | None = None,
) -> str:
    """Render one page (or the portal view) into the simulator's input format."""
    lines: list[str] = []

    def field(value: Any) -> str:
        """One input field, verbatim.

        Nothing is clipped here on purpose: the tool applies the firmware's own
        `safeCopyClip` (display fields) and `safeCopy` (identifiers) rules and
        *reports* what it had to do, so the preview and its findings always agree
        with the pad. Only newlines and tabs are flattened, because they would
        break the line-oriented format.
        """
        return _text(value).replace("\n", " ").replace("\r", " ").replace("\t", " ")

    if portal is not None:
        lines.append("portal 1")
        lines.append(
            "portalinfo\t{}\t{}\t{}".format(
                field(portal.get("ssid")),
                field(portal.get("password")),
                field(portal.get("address")),
            )
        )
    else:
        # The page id is an identifier (the tool reports an over-long one) while
        # the title is display-only.
        page_id = field(page.get("page_id")) or "page"
        lines.append(f"page {page_id} {field(page.get('title'))}")
        lines.append(
            f"state {int(selected)} {int(first_visible)} {1 if editing else 0}"
        )

    lines.append(f"net {max(0, min(4, int(network)))}")
    lines.append(f"usb {1 if usb_host else 0}")

    if portal is None:
        items: Iterable[Any] = page.get("items") or ()
        for item in items:
            if not isinstance(item, Mapping):
                continue
            lines.append(
                "item\t{}\t{}\t{}\t{}\t{}\t{}\t{}".format(
                    field(item.get("name")),
                    field(item.get("state")),
                    field(item.get("unit")),
                    _text(item.get("value", 0)),
                    field(item.get("type")) or "sensor",
                    field(item.get("entity")),
                    field(item.get("target_page")),
                )
            )

    for binding in keys:
        if not isinstance(binding, Mapping):
            continue
        lines.append(
            "key\t{}\t{}\t{}\t{}".format(
                field(binding.get("key_id")),
                field(binding.get("action")) or "none",
                field(binding.get("entity")),
                field(binding.get("target_page")),
            )
        )
    if press:
        lines.append(f"press {press}")
    return "\n".join(lines) + "\n"


def simulate(input_text: str, *, timeout: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """Run the simulator on ``input_text`` and return its JSON object.

    Raises :class:`SimulatorUnavailable` when the binary is missing and
    :class:`SimulatorError` when it fails or answers with invalid JSON.
    """
    path = simulator_path()
    if not simulator_available():
        raise SimulatorUnavailable(
            f"simulator not built at {path} — run scripts/build-simulator.sh"
        )
    try:
        # argv list, no shell: the input travels on stdin, so neither the path nor
        # the page text can be interpreted as shell syntax (S603 reviewed).
        completed = subprocess.run(  # noqa: S603
            [str(path)],
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:  # pragma: no cover - defensive
        raise SimulatorError("simulator timed out") from error
    except OSError as error:
        raise SimulatorUnavailable(f"simulator not runnable: {error}") from error

    stdout = completed.stdout.strip()
    if not stdout:
        raise SimulatorError(
            f"simulator produced no output (exit {completed.returncode})"
        )
    try:
        payload = json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError as error:
        raise SimulatorError(f"simulator output is not JSON: {error}") from error
    if not isinstance(payload, dict):
        raise SimulatorError("simulator output is not a JSON object")
    if payload.get("ok") is False:
        raise SimulatorError(str(payload.get("error") or "the simulator rejected this input"))
    return payload
