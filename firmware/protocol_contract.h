// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
#pragma once
#include <stddef.h>

namespace mp {

// The canonical wire-contract binding shared by the firmware sketch, the
// config generator, the /api/meta endpoint, and the browser. These constants
// must stay in lock-step with contracts/mqtt-contract.json; the host equality
// check (tests/cpp/test_protocol_contract.cpp) and
// scripts/test_firmware_contract.sh pin them to that file.
inline constexpr unsigned CONTRACT_VERSION = 1;
inline constexpr char TOPIC_EVENT[] = "micropad/event";
inline constexpr char TOPIC_PAGES[] = "micropad/pages/all";
inline constexpr char TOPIC_CURRENT_PAGE[] = "micropad/page/current";
inline constexpr char TOPIC_KEYMAP[] = "micropad/keymap";
inline constexpr char TOPIC_POWER[] = "micropad/power";
inline constexpr const char *KEY_IDS[] = {
    "r0c0", "r0c1", "r0c2", "r0c3", "r1c0", "r1c1", "r1c2", "r1c3",
    "r2c0", "r2c1", "r2c2", "r2c3", "enc_up", "enc_down"};
inline constexpr const char *ACTIONS[] = {
    "none", "enter", "back", "home", "settings", "scroll", "scroll_up",
    "scroll_down", "navigate", "keymap", "get_all_pages", "toggle", "on",
    "off", "press", "volume_up", "volume_down", "media_next", "media_prev",
    "edit", "confirm"};
inline constexpr size_t KEY_ID_COUNT = sizeof(KEY_IDS) / sizeof(KEY_IDS[0]);
inline constexpr size_t ACTION_COUNT = sizeof(ACTIONS) / sizeof(ACTIONS[0]);
}  // namespace mp