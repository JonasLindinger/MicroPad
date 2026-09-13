#!/usr/bin/env bash
# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# Integration Task 2 executable contract check (used by Task 7):
#  1. compile the host C++ equality check for firmware/protocol_contract.h,
#  2. verify every header string (topics, key ids, actions, version) appears
#     in contracts/mqtt-contract.json,
#  3. inspect the shipped sketch + core for the exact hardware/design facts.
set -euo pipefail
cd "$(dirname "$0")/.."

ROOT="$(pwd)"
mkdir -p build/host

echo "=== Step 1: host C++ equality check (tests/cpp/test_protocol_contract.cpp) ==="
g++ -std=c++17 -Wall -Wextra -Werror -pedantic \
  tests/cpp/test_protocol_contract.cpp -o build/host/test_protocol_contract
./build/host/test_protocol_contract
echo "host C++ equality check: exit 0"

echo "=== Step 2: header strings vs mqtt-contract.json + sketch/hardware facts ==="
python3 - "${ROOT}" <<'PY'
import json
import re
import sys

root = sys.argv[1]
fails = []


def require(ok, msg):
    if not ok:
        fails.append(msg)
    else:
        print("  PASS - " + msg)


def read(rel):
    with open(root + "/" + rel, encoding="utf-8") as handle:
        return handle.read()


header = read("firmware/protocol_contract.h")
contract = json.loads(read("contracts/mqtt-contract.json"))
ino = read("firmware/MicroPad_HA_Controller.ino")
core_h = read("firmware/micropad_core.h")
core_cpp = read("firmware/micropad_core.cpp")

# --- Step 2a: every header string must exist in the canonical JSON contract ---
topics = [
    match
    for match in re.findall(r'TOPIC_\w+\[\]\s*=\s*"([^"]+)"', header)
]
require(
    set(topics) == {topic["name"] for topic in contract["topics"].values()},
    "all five TOPIC_* strings match contracts/mqtt-contract.json topics",
)

def quoted_list(name):
    block = re.search(name + r"\[\]\s*=\s*\{(.*?)\}", header, re.S)
    return re.findall(r'"([^"]+)"', block.group(1))

key_ids = quoted_list("KEY_IDS")
actions = quoted_list("ACTIONS")
require(key_ids == contract["key_ids"], "KEY_IDS equals contract key_ids")
require(set(actions) == set(contract["actions"]), "ACTIONS equals contract actions")

version = int(re.search(r"CONTRACT_VERSION = (\d+)", header).group(1))
require(version == contract["version"], "CONTRACT_VERSION matches contract version")

# --- Step 2b: exact display / matrix / encoder / MQTT / sleep / display facts ---
require("PIN_EPD_CS = 8;" in ino and "PIN_EPD_DC = 40;" in ino
        and "PIN_EPD_RST = 41;" in ino and "PIN_EPD_BUSY = 42;" in ino,
        "display pins 8/40/41/42")
require("ROW_PINS[3] = {47, 48, 45};" in ino, "matrix rows 47/48/45")
require("COL_PINS[4] = {21, 14, 13, 12};" in ino, "matrix columns 21/14/13/12")
require("ENCODER_A_PIN = 10;" in ino and "ENCODER_B_PIN = 11;" in ino,
        "encoder pins 10/11")
require("setBufferSize" in ino and "MQTT_BUFFER_BYTES = 16384;" in core_h,
        "setBufferSize resolves to 16384 (MQTT_BUFFER_BYTES)")
require("QUEUE_CAPACITY = 16;" in core_h, "16-slot event queue")
require("MAX_FLUSH_PER_LOOP = 4;" in core_h
        and "MAX_FLUSH_PER_LOOP" in ino, "four-event loop flush bound")
require(ino.count("deserializeJson") == 1,
        "exactly one deserializeJson in the MQTT callback")
require(ino.count("if (isPowered())") == 2,
        "both immediate and caller-side isPowered() sleep guards")
require("USB_SAMPLE_MS = 500;" in core_h and "USB_STABLE_MS = 1500;" in core_h,
        "USB sample/stable intervals 500 and 1500")
require("setPartialWindow(0, 0, 128, 296)" in ino,
        "setPartialWindow(0, 0, 128, 296)")
require("display.init(0)" in ino, "display.init(0)")
require(ino.index("#define DISABLE_DIAGNOSTIC_OUTPUT")
        < ino.index("#include <GxEPD2_BW.h>"),
        "DISABLE_DIAGNOSTIC_OUTPUT before the GxEPD2 include")
require("digitalWrite(ROW_PINS[row], HIGH)" in ino,
        "active-row restoration to HIGH on every scan exit")
require("#define MICROPAD_DEBUG 0" in ino, "compile-time-off debug output")

# --- Step 2c: only the four approved source credential defaults ---
signature = "Settings defaultSettings()"
start = core_cpp.index(signature)
open_brace = core_cpp.index("{", core_cpp.index(") {", start))
depth = 0
for index in range(open_brace, len(core_cpp)):
    if core_cpp[index] == "{":
        depth += 1
    elif core_cpp[index] == "}":
        depth -= 1
        if depth == 0:
            body = core_cpp[open_brace + 1:index]
            break
require('"192.168.0.100"' in body and "1883" in body
        and '"micropad"' in body and '"replace-me"' in body,
        "neutral broker/port/user/password placeholders present")
require(body.count("safeCopy(value.") == 3 and "value.mqttPort = 1883" in body,
        "defaultSettings sets exactly the four approved credential defaults")
for banned in ("wifiSsid", "wifiPassword", "clientId"):
    require(("safeCopy(value." + banned) not in body,
            "no extra credential default beyond the approved four: " + banned)

if fails:
    print("\nFAILED firmware contract checks:")
    for failure in fails:
        print("  - " + failure)
    sys.exit(1)
print("\nAll " + "firmware contract checks passed.")
PY

echo "=== test_firmware_contract.sh: ALL CHECKS PASSED ==="