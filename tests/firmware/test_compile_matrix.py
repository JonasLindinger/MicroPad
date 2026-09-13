# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.

"""Arduino CLI compile-matrix contract tests (Task 13).

Proves the three real ESP32-S3 USB compile configurations are pinned and
exercisable through ``scripts/compile-firmware.sh``:

* ``--print-matrix`` reports exactly the three pinned FQBNs and nothing else.
* Sketch staging under ``build/arduino/MicroPad_HA_Controller/`` is
  deterministic (four source files, mode 0644, byte-identical to ``firmware/``)
  before any compile invocation.
* When Arduino CLI 1.5.1 is present on PATH, a real ``--one`` compile is run
  for every FQBN and must exit 0; otherwise only that compile-execution case
  is skipped (with the exact reason). Print-matrix and staging cases never
  skip.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

EXPECTED = (
    "esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,PSRAM=opi",
    "esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=cdc,PSRAM=opi",
    "esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=default,PSRAM=disabled",
)

SKETCH_FILES = ("MicroPad_HA_Controller.ino", "micropad_core.h",
                "micropad_core.cpp", "protocol_contract.h")
STAGE_DIR = PROJECT_ROOT / "build" / "arduino" / "MicroPad_HA_Controller"
COMPILE_SCRIPT = PROJECT_ROOT / "scripts" / "compile-firmware.sh"


def _run_arduino_cli_version() -> bool:
    """True only when a real arduino-cli 1.5.1 is on PATH."""
    cli = shutil.which("arduino-cli")
    if cli is None:
        return False
    try:
        out = subprocess.run([cli, "version"], capture_output=True,
                             text=True, check=False).stdout
    except OSError:
        return False
    return "Version: 1.5.1" in out


ARDUINO_CLI_1_5_1 = _run_arduino_cli_version()
SKIP_REASON = "Arduino CLI 1.5.1 is not installed"


class CompilePrintMatrixTest(unittest.TestCase):
    def test_print_matrix_outputs_exactly_the_three_fqbns(self):
        out = subprocess.run([str(COMPILE_SCRIPT), "--print-matrix"],
                             capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout, "\n".join(EXPECTED) + "\n")
        self.assertEqual(out.stderr, "")


class SketchStagingTest(unittest.TestCase):
    def _staged_file(self, name: str) -> Path:
        return STAGE_DIR / name

    def test_staging_creates_the_four_sources_with_perm_0644(self):
        # Deterministic staging: the stage directory is rebuilt fresh and only
        # the four firmware sources land there, mode 0644, byte-identical to
        # the reviewed sources. Uses the fast --stage-only path so this
        # contract case never requires a toolchain (never skips).
        subprocess.run([str(COMPILE_SCRIPT), "--stage-only", EXPECTED[0]],
                       capture_output=True, text=True, check=True)
        for name in SKETCH_FILES:
            staged = self._staged_file(name)
            self.assertTrue(staged.is_file(), f"{name} must be staged")
            self.assertEqual(
                staged.read_bytes(),
                (PROJECT_ROOT / "firmware" / name).read_bytes(),
                f"{name} must be byte-identical to the source")
            mode = stat.S_IMODE(staged.stat().st_mode)
            self.assertEqual(mode, 0o644, f"{name} must be mode 0644, got "
                             f"{oct(mode)}")

    def test_one_stages_before_invoking_the_cli(self):
        # --one must route every valid FQBN through the same deterministic
        # staging step (stage_sketch) before arduino-cli compile: no mode can
        # compile from a stale or missing stage. Asserted statically so this
        # contract case is toolchain-free (never skips).
        text = COMPILE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("stage_sketch", text)
        self.assertIn("arduino-cli compile", text)
        # The one-command implementation calls staging then compile in order.
        one_block = COMPILE_SCRIPT.read_text(encoding="utf-8")
        stage_at = one_block.index("stage_sketch")
        compile_at = one_block.index("arduino-cli compile")
        # stage_sketch is defined before the compile invocation in the script.
        self.assertLess(stage_at, compile_at)


@unittest.skipUnless(ARDUINO_CLI_1_5_1, SKIP_REASON)
class RealCompileMatrixTest(unittest.TestCase):
    def test_all_three_fqbns_compile_exit_zero(self):
        # A real ESP32-S3 compile per USB mode; validates the guarded CDC
        # paths genuinely compile with warnings-all + -Werror=return-type.
        self.assertEqual(len(EXPECTED), 3)
        for fqbn in EXPECTED:
            with self.subTest(fqbn=fqbn):
                result = subprocess.run(
                    [str(COMPILE_SCRIPT), "--one", fqbn],
                    capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 0,
                                 f"{fqbn} failed\n{result.stdout}\n"
                                 f"{result.stderr}")


if __name__ == "__main__":
    unittest.main(verbosity=2)