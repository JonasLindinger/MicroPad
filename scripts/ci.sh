#!/usr/bin/env bash
# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
#
# Reproducible clean-room CI orchestration (Integration Task 7).
#
# Each named stage is run in a fixed order and its full transcript is captured
# to build/evidence/<stage>.log (git-ignored). A machine-readable record of
# {name, exit_code, duration_s, log_sha256} is appended to
# build/evidence/stages.jsonl for every stage. The script stops at the first
# failing stage and replays that stage's failing output to stderr; it never
# suppresses a failure and never continues past one. Running the same clean
# build twice reproduces the same ordered stages with exit_code 0 (durations
# and hashes are permitted to vary, but the stage names and outcomes must not).
#
# Python stages are deliberately scoped to tests/unit|api|integration|browser|
# deployment|release — never `pytest tests/` unqualified — so the real ESP32-S3
# compile matrix in tests/firmware/test_compile_matrix.py is exercised only as
# the explicit firmware-hwcdc / firmware-tinyusb device build stages, never left
# to hang or race inside a broad pytest run (FE2 carry).
set -Eeuo pipefail
cd "$(dirname "$0")/.."

EVIDENCE_DIR="build/evidence"
STAGES_FILE="$EVIDENCE_DIR/stages.jsonl"
mkdir -p "$EVIDENCE_DIR"
: > "$STAGES_FILE"

now_ms() {
  date +%s%3N 2>/dev/null || date +%s000
}

# run_stage <name> <cmd...>
#   Captures the command's transcript to build/evidence/<name>.log, appends one
#   JSON record to stages.jsonl, prints a summary, and re-emits the failing log
#   when the stage fails. Under `set -e`, a non-zero return aborts the build.
run_stage() {
  local name="$1"; shift
  local log="$EVIDENCE_DIR/$name.log"
  local start_ms end_ms rc sha duration_ms
  start_ms="$(now_ms)"
  set +e
  "$@" > "$log" 2>&1
  rc=$?
  set -e
  end_ms="$(now_ms)"
  duration_ms=$(( end_ms - start_ms ))
  sha="$(sha256sum "$log" | awk '{print $1}')"
  printf '{"name":"%s","exit_code":%d,"duration_s":%.3f,"log_sha256":"%s"}\n' \
    "$name" "$rc" "$(awk -v d="$duration_ms" 'BEGIN{printf "%.3f", d/1000}')" \
    "$sha" >> "$STAGES_FILE"
  if [[ "$rc" -ne 0 ]]; then
    printf 'STAGE %s FAILED (exit %d) — replaying log\n' "$name" "$rc" >&2
    cat "$log" >&2 || true
    return "$rc"
  fi
  printf 'STAGE %s OK (%ss)\n' \
    "$name" "$(awk -v d="$duration_ms" 'BEGIN{printf "%.3f", d/1000}')"
  return 0
}

# The mandated stage sequence (order is asserted by
# tests/release/test_build_orchestration.py). The leading toolchain stage makes
# `make ci` self-sufficient after a clean `rm -rf .arduino` (Step 6): it installs
# and version-verifies the pinned Arduino CLI 1.5.1 core + libraries. On GitHub
# Actions the workflow caches this exact toolchain and runs the same install, so
# this stage is quick there and idempotent everywhere.
run_stage "toolchain"       bash scripts/install-arduino-toolchain.sh
run_stage "python-unit"      .venv/bin/pytest tests/test_*.py -q
run_stage "mqtt-contract"     .venv/bin/pytest tests/integration/test_mqtt_contract.py -q
run_stage "fake-ha-roundtrip" .venv/bin/pytest tests/integration/test_fake_ha_roundtrip.py -q
run_stage "frontend"          .venv/bin/pytest tests/browser -q
run_stage "firmware-host"     bash scripts/test_firmware_contract.sh
run_stage "firmware-hwcdc"    bash scripts/compile-firmware.sh hwcdc
run_stage "firmware-tinyusb"  bash scripts/compile-firmware.sh tinyusb
run_stage "deployment"        .venv/bin/pytest tests/deployment -q
run_stage "docs-notice"       bash -c '.venv/bin/pytest tests/release/test_docs_release.py -q && .venv/bin/python scripts/audit_notices.py'
# Local/pre-push safe: exercise synthetic verifier fixtures only. The real CLI
# remains a post-push operator action and ordinary CI needs no captured GitHub
# JSON, anonymous clone, target commit, hardware report, or private config.
run_stage "post-push-verifier-unit" .venv/bin/pytest tests/release/test_post_push_verification.py -q
run_stage "public-audit"      .venv/bin/pytest tests/release -q

printf '\nAll %s stages passed; evidence under %s/\n' \
  "$(grep -c '^' "$STAGES_FILE")" "$EVIDENCE_DIR"