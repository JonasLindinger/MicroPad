// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
#include "../../firmware/protocol_contract.h"
#include <cassert>
#include <cstring>

int main() {
  // Contract revision 3 added the `adjust` action (encoder turn on a slider row)
  // and the `hold`/`double` gesture targets plus the item `control` field.
  static_assert(mp::CONTRACT_VERSION == 3);
  static_assert(mp::KEY_ID_COUNT == 14);
  static_assert(mp::ACTION_COUNT == 22);
  assert(std::strcmp(mp::ACTIONS[21], "adjust") == 0);
  assert(std::strcmp(mp::TOPIC_EVENT, "micropad/event") == 0);
  // Device info is retained like the other state topics, so a late
  // subscriber (backend, UI) still learns the pad's firmware build.
  assert(std::strcmp(mp::TOPIC_DEVICE, "micropad/device") == 0);
  assert(std::strcmp(mp::KEY_IDS[12], "enc_up") == 0);
  assert(std::strcmp(mp::KEY_IDS[13], "enc_down") == 0);
  return 0;
}