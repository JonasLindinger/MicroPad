#!/usr/bin/env bash
# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
#
# Build the host simulator tools/micropad_sim.cpp, which drives the real firmware
# core (firmware/micropad_core.cpp) so the configurator preview and the config lint
# render what the pad would render. No toolchain needed: the core is
# dependency-free, so plain g++ is enough.
#
#   ./scripts/build-simulator.sh            # build into build/host/micropad_sim
#   ./scripts/build-simulator.sh --print    # print the binary path only
set -euo pipefail
cd "$(dirname "$0")/.."

ROOT="$(pwd)"
OUT="${ROOT}/build/host/micropad_sim"
mkdir -p "${ROOT}/build/host"

if [[ "${1:-}" == "--print" ]]; then
  printf '%s\n' "${OUT}"
  exit 0
fi

g++ -std=c++17 -Wall -Wextra -Werror -pedantic -Ifirmware \
  tools/micropad_sim.cpp firmware/micropad_core.cpp \
  -o "${OUT}"
printf 'built %s\n' "${OUT}"
