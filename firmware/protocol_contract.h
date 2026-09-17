// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
#pragma once
#include <stddef.h>

namespace mp {

// GENERATED FILE — do not edit by hand. Produced by
// scripts/generate_contract.py from contracts/mqtt-contract.json, the canonical
// wire-contract binding shared by the firmware sketch, the config generator,
// the /api/meta endpoint and the browser. Change the contract and run
// `python3 scripts/generate_contract.py --write`; `--check` fails on drift.
inline constexpr unsigned CONTRACT_VERSION = 3;
inline constexpr char TOPIC_CURRENT_PAGE[] = "micropad/page/current";
inline constexpr char TOPIC_DEVICE[] = "micropad/device";
inline constexpr char TOPIC_DIAG[] = "micropad/diag";
inline constexpr char TOPIC_EVENT[] = "micropad/event";
inline constexpr char TOPIC_KEYMAP[] = "micropad/keymap";
inline constexpr char TOPIC_PAGES[] = "micropad/pages/all";
inline constexpr char TOPIC_POWER[] = "micropad/power";
inline constexpr const char *KEY_IDS[] = {
    "r0c0", "r0c1", "r0c2", "r0c3", "r1c0", "r1c1", "r1c2", "r1c3", "r2c0",
    "r2c1", "r2c2", "r2c3", "enc_up", "enc_down",
};
inline constexpr const char *ACTIONS[] = {
    "none", "enter", "back", "home", "settings", "scroll", "scroll_up",
    "scroll_down", "navigate", "keymap", "get_all_pages", "toggle", "on",
    "off", "press", "volume_up", "volume_down", "media_next", "media_prev",
    "edit", "confirm", "adjust",
};
inline constexpr size_t KEY_ID_COUNT = sizeof(KEY_IDS) / sizeof(KEY_IDS[0]);
inline constexpr size_t ACTION_COUNT = sizeof(ACTIONS) / sizeof(ACTIONS[0]);
}  // namespace mp
