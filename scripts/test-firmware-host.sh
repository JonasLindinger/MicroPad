#!/usr/bin/env bash
# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build/host
g++ -std=c++17 -Wall -Wextra -Werror -pedantic -Ifirmware \
  tests/firmware/test_core.cpp firmware/micropad_core.cpp \
  -o build/host/test_core
./build/host/test_core
python3 tests/firmware/test_firmware_static.py -v
python3 tests/firmware/test_compile_matrix.py -v