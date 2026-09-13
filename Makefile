# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
#
# Reproducible clean-room build entry points (Integration Task 7).
#
# Targets:
#   make test      Python unit/API/integration tests (fast; no device build)
#   make browser   Install pinned Playwright Chromium, run browser tests
#   make firmware  Host contract + both device firmware groups (hwcdc, tinyusb)
#   make audit     Release notice + public-tree audits
#   make ci        Full staged orchestration via scripts/ci.sh
#
# All local/generated state (build/, .venv/, .arduino/) is git-ignored.
.PHONY: help test browser firmware audit ci

help:
	@echo "targets: test browser firmware audit ci"

# Scope Python stages explicitly (tests/test_*.py covers unit+api top-level
# tests; tests/integration for the MQTT/round-trip integration tests). Never
# run bare `pytest tests/`, which would trigger the real ESP32-S3 compile
# matrix under tests/firmware (FE2 carry).
test:
	.venv/bin/pytest tests/test_*.py tests/integration -q

browser:
	.venv/bin/python -m playwright install chromium
	.venv/bin/pytest tests/browser -q

firmware:
	bash scripts/test_firmware_contract.sh
	bash scripts/compile-firmware.sh hwcdc
	bash scripts/compile-firmware.sh tinyusb

audit:
	.venv/bin/python scripts/audit_notices.py
	.venv/bin/pytest tests/release -q

ci:
	bash scripts/ci.sh