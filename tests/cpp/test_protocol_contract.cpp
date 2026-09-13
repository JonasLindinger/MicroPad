// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
#include "../../firmware/protocol_contract.h"
#include <cassert>
#include <cstring>

int main() {
  static_assert(mp::CONTRACT_VERSION == 1);
  static_assert(mp::KEY_ID_COUNT == 14);
  static_assert(mp::ACTION_COUNT == 21);
  assert(std::strcmp(mp::TOPIC_EVENT, "micropad/event") == 0);
  assert(std::strcmp(mp::KEY_IDS[12], "enc_up") == 0);
  assert(std::strcmp(mp::KEY_IDS[13], "enc_down") == 0);
  return 0;
}