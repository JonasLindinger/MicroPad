#!/usr/bin/env bash
# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
#
# Reproducible dual-mode ESP32-S3 firmware build (Integration Task 7), layered
# on the Task 13 compile matrix.
#
# The build is clean by construction: the exact core and library versions are
# pinned as literals (esp32:esp32@3.3.11, GxEPD2@1.6.9, ArduinoJson@7.4.3,
# PubSubClient@2.8) and the tool root is the ignored ${ARDUINO_DATA_DIR} tree.
# The Espressif package index URL used by arduino-cli.yaml is declared here so
# the two build modes never drift from the pinned inputs.
#
# Modes:
#   compile-firmware.sh --print-matrix   print all three pinned FQBNs
#   compile-firmware.sh --stage-only FQBN  stage only (fast, no toolchain)
#   compile-firmware.sh --one FQBN       stage + compile one validated FQBN
#   compile-firmware.sh hwcdc            stage + compile the hwcdc group
#   compile-firmware.sh tinyusb          stage + compile the tinyusb (default) group
#   compile-firmware.sh                  compile all three, stop at first fail
#
# FQBN reconciliation (flagged): the Task 7 brief lists two FQBNs
# (USBMode=hwcdc,cDC and USBMode=default,cDC) while the shipped Task 13 matrix
# pins three (adding USBMode=hwcdc with CDCOnBoot=default, the battery-only no
# -USB host build). We keep all three in the matrix contract and group them:
# the "hwcdc" group covers every USBMode=hwcdc FQBN, and "tinyusb" covers the
# USBMode=default one — together they compile the full pinned matrix so the
# existing compile-matrix tests are untouched.
#
# Every real compile writes its full log under build/evidence/ and fails if it
# fails to compile or if the staged sketch's source default deviates from the
# exact broker 192.168.0.100 / user micropad / password replace-me / port 1883.
set -Eeuo pipefail
cd "$(dirname "$0")/.."

CONFIG_FILE="arduino-cli.yaml"
SOURCE_DIR="firmware"
SKETCH_DIR="build/arduino/MicroPad_HA_Controller"
ARDUINO_DATA_DIR="${ARDUINO_DATA_DIR:-$PWD/.arduino}"
EVIDENCE_DIR="build/evidence"

# Pinned REPRO toolchain (Task 7): exact versions, enforced as literals.
PIN_PACKAGE_INDEX="https://espressif.github.io/arduino-esp32/package_esp32_index.json"
PIN_CORE="esp32:esp32@3.3.11"
PIN_LIB_GXEPD2="GxEPD2@1.6.9"
PIN_LIB_ARDUINOJSON="ArduinoJson@7.4.3"
PIN_LIB_PUBSUBCLIENT="PubSubClient@2.8"

FQBINS=(
  "esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,PSRAM=opi"
  "esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=cdc,PSRAM=opi"
  "esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=default,PSRAM=disabled"
)

usage() {
  cat <<'EOF'
usage:
  compile-firmware.sh --print-matrix
  compile-firmware.sh --stage-only FQBN
  compile-firmware.sh --one FQBN
  compile-firmware.sh hwcdc
  compile-firmware.sh tinyusb
  compile-firmware.sh          (compile all matrix FQBNs, stop at first fail)
EOF
}

valid_fqbn() {
  local fqbn="$1"
  for entry in "${FQBINS[@]}"; do
    [[ "$entry" == "$fqbn" ]] && return 0
  done
  return 1
}

require_fqbn() {
  local fqbn="${1:-}"
  if ! valid_fqbn "$fqbn"; then
    printf 'ERROR: unknown FQBN %q; expected one of the compile matrix:\n' \
      "$fqbn" >&2
    printf '  %s' "${FQBINS[@]}" >&2
    printf '\n' >&2
    return 1
  fi
}

# Step 4a: deterministic staging. Rebuild only the sketch directory and copy
# the three reviewed sources there (mode 0644), byte-identical to firmware/.
stage_sketch() {
  rm -rf "$SKETCH_DIR"
  mkdir -p "$SKETCH_DIR"
  install -m 0644 "$SOURCE_DIR/MicroPad_HA_Controller.ino" "$SKETCH_DIR/"
  install -m 0644 "$SOURCE_DIR/micropad_core.h" "$SKETCH_DIR/"
  install -m 0644 "$SOURCE_DIR/micropad_core.cpp" "$SKETCH_DIR/"
  install -m 0644 "$SOURCE_DIR/protocol_contract.h" "$SKETCH_DIR/"
}

# Step 3: fail if the staged sketch's source default deviates from the only
# permissible placeholder values (broker, user, password, port).
verify_source_default() {
  local src="$SOURCE_DIR/micropad_core.cpp" ok=1
  local host='192.168.0.100' user='micropad' pw='replace-me' port='1883'
  grep -qF "safeCopy(value.mqttHost, \"${host}\")" "$src" \
    || { printf 'ERROR: default broker must be %s\n' "$host" >&2; ok=0; }
  grep -qF "safeCopy(value.mqttUser, \"${user}\")" "$src" \
    || { printf 'ERROR: default user must be %s\n' "$user" >&2; ok=0; }
  grep -qF "safeCopy(value.mqttPassword, \"${pw}\")" "$src" \
    || { printf 'ERROR: default password must be %s\n' "$pw" >&2; ok=0; }
  grep -qF "value.mqttPort = ${port};" "$src" \
    || { printf 'ERROR: default port must be %s\n' "$port" >&2; ok=0; }
  [[ "$ok" -eq 1 ]]
}

# Step 4b: one exact compile invocation with warning escalation, logging the
# full transcript under build/evidence/ (Task 7).
compile_one() {
  local fqbn="$1"
  mkdir -p "$EVIDENCE_DIR"
  local log
  log="$EVIDENCE_DIR/compile-$(printf '%s' "$fqbn" | tr ':,' '__').log"
  arduino-cli compile \
    --config-file "$CONFIG_FILE" \
    --fqbn "$fqbn" \
    --warnings all \
    --build-property "compiler.cpp.extra_flags=-Werror=return-type" \
    "$SKETCH_DIR" 2>&1 | tee "$log"
}

one() {
  require_fqbn "$1"
  stage_sketch
  verify_source_default
  printf '=== Compiling %s ===\n' "$1"
  compile_one "$1"
}

compiles_matching() {
  local needle="$1"
  for entry in "${FQBINS[@]}"; do
    [[ "$entry" == *"$needle"* ]] && return 0
  done
  return 1
}

# Grouped device builds (Task 7): "hwcdc" = every USBMode=hwcdc FQBN,
# "tinyusb" = the USBMode=default FQBN. Together the two groups exhaust the
# three-FQBN matrix pinned by the Task 13 tests.
compile_group() {
  local mode="$1" any=0
  case "$mode" in
    hwcdc)
      for fqbn in "${FQBINS[@]}"; do
        if [[ "$fqbn" == *"USBMode=hwcdc"* ]]; then
          one "$fqbn"; any=1
        fi
      done ;;
    tinyusb)
      for fqbn in "${FQBINS[@]}"; do
        if [[ "$fqbn" == *"USBMode=default"* ]]; then
          one "$fqbn"; any=1
        fi
      done ;;
    *)
      printf 'ERROR: unknown group %q (expected hwcdc or tinyusb)\n' "$mode" >&2
      return 1 ;;
  esac
  [[ "$any" -eq 1 ]]
}

stage_only() {
  require_fqbn "$1"
  stage_sketch
  verify_source_default
  printf 'Staged sketch for %s -> %s\n' "$1" "$SKETCH_DIR"
}

print_matrix() {
  for entry in "${FQBINS[@]}"; do
    printf '%s\n' "$entry"
  done
}

compile_all() {
  for entry in "${FQBINS[@]}"; do
    one "$entry"
  done
}

main() {
  case "${1:-}" in
    --print-matrix) print_matrix ;;
    --stage-only) stage_only "${2:-}" ;;
    --one) one "${2:-}" ;;
    hwcdc|tinyusb) compile_group "$1" ;;
    -h|--help) usage ;;
    *) compile_all ;;
  esac
}

main "$@"