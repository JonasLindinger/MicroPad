#!/usr/bin/env bash
# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
#
# Pin and install the REPRO Arduino toolchain for the MicroPad firmware:
#   Arduino CLI 1.5.1, esp32 core 3.3.11, GxEPD2 1.6.9, ArduinoJson 7.4.3,
#   PubSubClient 2.8.
# The arduino-cli binary itself must already be on PATH (CI installs 1.5.1 via
# arduino/setup-arduino-cli@v2; locally download it from the GitHub release);
# this script rejects any other version and then installs/verifies the pinned
# core and libraries into the repo-local .arduino/ tree (gitignored).
set -euo pipefail
cd "$(dirname "$0")/.."

CONFIG_FILE="arduino-cli.yaml"

# Step 3b: hard version gate on the CLI itself.
VERSION_OUT="$(arduino-cli version 2>&1 || true)"
if [[ "$VERSION_OUT" != *"Version: 1.5.1"* ]]; then
  printf 'ERROR: Arduino CLI 1.5.1 required (got):\n%s\n' "$VERSION_OUT" >&2
  exit 1
fi
printf 'Arduino CLI OK: %s\n' "$VERSION_OUT"

# Step 3b: refresh the ESP32 board index.
arduino-cli core update-index --config-file "$CONFIG_FILE"

# Step 3c: install the pinned ESP32 core and verify the reported version.
arduino-cli core install esp32:esp32@3.3.11 --config-file "$CONFIG_FILE"
CORE_LIST="$(arduino-cli core list --config-file "$CONFIG_FILE" || true)"
if [[ "$CORE_LIST" != *"esp32:esp32"* ]]; then
  printf 'ERROR: esp32:esp32 core is not installed:\n%s\n' "$CORE_LIST" >&2
  exit 1
fi
INSTALLED_CORE="$(awk '$1 == "esp32:esp32" { print $2 }' <<<"$CORE_LIST")"
if [[ "$INSTALLED_CORE" != "3.3.11" ]]; then
  printf 'ERROR: expected esp32:esp32 3.3.11, got %s\n' "$INSTALLED_CORE" >&2
  exit 1
fi
printf 'ESP32 core OK: esp32:esp32 %s\n' "$INSTALLED_CORE"

# Step 3d: install the pinned libraries.
arduino-cli lib install GxEPD2@1.6.9 --config-file "$CONFIG_FILE"
arduino-cli lib install ArduinoJson@7.4.3 --config-file "$CONFIG_FILE"
arduino-cli lib install PubSubClient@2.8 --config-file "$CONFIG_FILE"

# Step 3d: verify all three library versions.
LIB_LIST="$(arduino-cli lib list --config-file "$CONFIG_FILE" || true)"
declare -A EXPECTED=(
  ["GxEPD2"]="1.6.9"
  ["ArduinoJson"]="7.4.3"
  ["PubSubClient"]="2.8"
)
for name in "${!EXPECTED[@]}"; do
  version="${EXPECTED[$name]}"
  line="$(awk -v n="$name" '$1 == n { print $0 }' <<<"$LIB_LIST")"
  if [[ -z "$line" ]] || [[ "$line" != *"$version"* ]]; then
    printf 'ERROR: %s is not installed at %s:\n%s\n' \
      "$name" "$version" "$LIB_LIST" >&2
    exit 1
  fi
  printf 'Library OK: %s %s\n' "$name" "$version"
done

printf 'Arduino toolchain pinned and verified (CLI 1.5.1, core 3.3.11, '
printf 'GxEPD2 1.6.9, ArduinoJson 7.4.3, PubSubClient 2.8).\n'