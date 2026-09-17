// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
//
// micropad_sim — the firmware's own core, driven from the host.
//
// The configurator's panel preview and the config lint must show what the pad will
// really render, so this tool runs the shipped core instead of a second
// implementation: it builds a RenderSnapshot, asks the core's layout pass for the
// prim list, resolves what a key press does through the core's descriptor table,
// and reports the fields the pad would clip or reject.
//
// This file contains no drawing, geometry or policy of its own; the only logic
// here is reading the input below and printing JSON. A panel-layout change shows
// up in the preview precisely because the preview *is* the firmware.
//
// Usage:
//     micropad_sim < payload.txt        # one JSON object on stdout
//     micropad_sim --version | --help
//
// Input (one directive per line; `#` starts a comment; item and key fields are
// TAB-separated so display names may contain spaces):
//
//     page <page_id> <title>                     # required, once ("page home Home")
//     state <selected> <first_visible> <editing> # 0/1 editing; default 0 0 0
//     net <0..4>                                 # network status; default 0
//     usb <0|1>                                  # USB host present; default 0
//     portal <0|1>                               # default 0
//     portalinfo <ssid>\t<password>\t<address>   # required when portal is 1
//     item <name>\t<state>\t<unit>\t<value>\t<type>\t<entity>\t<target_page>[\t<min>\t<max>\t<step>\t<control>]
//     key <key_id>\t<action>\t<entity>\t<target_page>[\t<hold_action>\t<hold_entity>\t<hold_target_page>\t<double_action>\t<double_entity>\t<double_target_page>]   # an effective binding
//     press <key_id> [tap|hold|double]           # resolve what pressing it does
//
// Output (single JSON object):
//
//     {"ok":true,"mode":"normal","title":"Home","total_items":3,"first_visible":0,
//      "row_count":3,"rows":[{"name":...,"style":"checkbox"}...],"prims":[{"kind":"rect",...}],
//      "prims_truncated":false,"would_reject":false,
//      "findings":[{"code":"field_clipped","severity":"warning",...}],
//      "press":{"key":"r0c3","gesture":"tap","binding_action":"enter","item_action":"toggle",
//               "entity":"light.desk","target_page":""}}
//
// Exit codes: 0 = rendered (inspect "would_reject"/"findings"), 2 = malformed
// input (a JSON error object is printed as well), 3 = usage error.

#include "micropad_core.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

namespace {

using micropad::Action;
using micropad::AppState;
using micropad::Binding;
using micropad::BindingTarget;
using micropad::InputEvent;
using micropad::Item;
using micropad::ItemType;
using micropad::KeyId;
using micropad::Page;
using micropad::PrimKind;
using micropad::RenderModel;
using micropad::RenderPrim;
using micropad::RenderRow;
using micropad::RenderSnapshot;
using micropad::RowStyle;
using micropad::TextAlign;

constexpr size_t MAX_ITEMS = micropad::MAX_ITEMS_PER_PAGE;
constexpr size_t MAX_BINDINGS = micropad::KEY_COUNT;

struct Finding {
  const char *code;
  const char *severity;  // "warning" = clipped on display, "error" = payload rejected
  std::string field;
  size_t cap;
  size_t chars;
};

struct KeyBinding {
  KeyId key;
  Binding binding;
};

struct Input {
  Page page{};
  AppState state{};
  RenderSnapshot snapshot{};
  bool portal = false;
  char portalSsid[33] = {};
  char portalPassword[micropad::SETUP_PASSWORD_LENGTH + 1] = {};
  char portalAddress[16] = {};
  bool hasPress = false;
  KeyId pressKey = KeyId::R0C0;
  Binding pressBinding{};
  bool hasBinding = false;
  // Which gesture the press resolves as: 0 tap, 1 hold, 2 double.
  uint8_t pressGesture = 0;
  KeyBinding bindings[MAX_BINDINGS]{};
  size_t bindingCount = 0;
  std::vector<Finding> findings;
  bool wouldReject = false;
  bool malformed = false;
  std::string malformedReason;
};

std::string jsonEscape(const char *text) {
  std::string out;
  for (const unsigned char *p = reinterpret_cast<const unsigned char *>(text);
       *p != '\0'; ++p) {
    switch (*p) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (*p < 0x20) {
          char buffer[8];
          std::snprintf(buffer, sizeof(buffer), "\\u%04x", static_cast<unsigned>(*p));
          out += buffer;
        } else {
          out += static_cast<char>(*p);
        }
        break;
    }
  }
  return out;
}

std::vector<std::string> split(const std::string &text, char separator) {
  std::vector<std::string> parts;
  size_t start = 0;
  for (;;) {
    const size_t at = text.find(separator, start);
    if (at == std::string::npos) {
      parts.push_back(text.substr(start));
      return parts;
    }
    parts.push_back(text.substr(start, at - start));
    start = at + 1;
  }
}

std::string trim(const std::string &text) {
  const size_t first = text.find_first_not_of(" \t\r\n");
  if (first == std::string::npos) return "";
  const size_t last = text.find_last_not_of(" \t\r\n");
  return text.substr(first, last - first + 1);
}

// The payload after the directive keyword. A single space or TAB separates them
// ("item\tname..." is valid TSV, "item name..." is what a human writes); the rest
// of the line is kept verbatim, because the fields are TAB-separated and display
// names may contain spaces or start with an empty field.
std::string directivePayload(const std::string &trimmed) {
  const size_t space = trimmed.find(' ');
  const size_t tab = trimmed.find('\t');
  size_t at = std::min(space, tab);
  if (at == std::string::npos) return "";
  std::string payload = trimmed.substr(at + 1);
  if (!payload.empty() && payload.back() == '\r') payload.pop_back();
  return payload;
}

// The leading keyword of a directive line.
std::string directiveKeyword(const std::string &trimmed) {
  const size_t space = trimmed.find(' ');
  const size_t tab = trimmed.find('\t');
  const size_t at = std::min(space, tab);
  return at == std::string::npos ? trimmed : trimmed.substr(0, at);
}

// A display-only field: the pad clips it, it never rejects the payload for being
// long (name/title/state/unit).
template <size_t N>
void copyDisplay(char (&dst)[N], const std::string &src, const char *field, Input &input) {
  const size_t cap = N - 1;
  if (src.size() > cap) {
    input.findings.push_back({"field_clipped", "warning", field, cap, src.size()});
  }
  micropad::safeCopyClip(dst, src.c_str());
}

// An identifier: a too-long value is a hard parse failure on the pad, so the
// whole payload would be discarded — the caller must never publish it.
template <size_t N>
bool copyIdentifier(char (&dst)[N], const std::string &src, const char *field,
                    Input &input) {
  if (src.size() <= N - 1 && micropad::safeCopy(dst, src.c_str())) return true;
  input.findings.push_back({"identifier_too_long", "error", field, N - 1, src.size()});
  input.wouldReject = true;
  return false;
}

bool parseBool(const std::string &token, bool &out) {
  if (token == "0" || token == "false") {
    out = false;
    return true;
  }
  if (token == "1" || token == "true") {
    out = true;
    return true;
  }
  return false;
}

void fail(Input &input, const std::string &reason) {
  input.malformed = true;
  input.malformedReason = reason;
}

bool parseInt(const std::string &token, long &out) {
  char *end = nullptr;
  const long value = std::strtol(token.c_str(), &end, 10);
  if (end == nullptr || *end != '\0' || token.empty()) return false;
  out = value;
  return true;
}

bool parseFloat(const std::string &token, float &out) {
  char *end = nullptr;
  const double value = std::strtod(token.c_str(), &end);
  if (end == nullptr || *end != '\0' || token.empty()) return false;
  out = static_cast<float>(value);
  return true;
}

bool parseItem(const std::string &payload, Input &input) {
  const std::vector<std::string> fields = split(payload, '\t');
  if (fields.size() < 5) {
    fail(input, "item needs name\\tstate\\tunit\\tvalue\\ttype[\\tentity\\ttarget_page]");
    return false;
  }
  if (input.page.itemCount >= MAX_ITEMS) {
    fail(input, "more items than MAX_ITEMS_PER_PAGE (" + std::to_string(MAX_ITEMS) + ")");
    return false;
  }
  char nameField[40];
  std::snprintf(nameField, sizeof(nameField), "items[%u].name",
                static_cast<unsigned>(input.page.itemCount));
  Item &item = input.page.items[input.page.itemCount];
  item = Item{};
  copyDisplay(item.name, fields[0], nameField, input);
  copyDisplay(item.state, fields[1], "items[].state", input);
  copyDisplay(item.unit, fields[2], "items[].unit", input);
  if (!parseFloat(fields[3], item.value)) {
    fail(input, "item value must be a number");
    return false;
  }
  if (!micropad::parseItemType(fields[4].c_str(), item.type)) {
    fail(input, "unknown item type '" + fields[4] + "'");
    return false;
  }
  if (fields.size() > 5) copyIdentifier(item.entity, fields[5], "items[].entity", input);
  if (fields.size() > 6) {
    copyIdentifier(item.targetPage, fields[6], "items[].target_page", input);
  }
  // Range and control are optional (the preview existed before sliders did), so
  // a page that carries neither still renders exactly as it did.
  if (fields.size() > 7) {
    if (!parseFloat(fields[7], item.min)) {
      fail(input, "item min must be a number");
      return false;
    }
  }
  if (fields.size() > 8) {
    if (!parseFloat(fields[8], item.max)) {
      fail(input, "item max must be a number");
      return false;
    }
  }
  if (fields.size() > 9) {
    if (!parseFloat(fields[9], item.step)) {
      fail(input, "item step must be a number");
      return false;
    }
  }
  if (fields.size() > 10 && !fields[10].empty()) {
    if (!micropad::parseControl(fields[10].c_str(), item.control)) {
      fail(input, "unknown item control '" + fields[10] + "'");
      return false;
    }
  }
  ++input.page.itemCount;
  return true;
}

bool parseBinding(const std::string &payload, Input &input, Binding &binding) {
  const std::vector<std::string> fields = split(payload, '\t');
  if (fields.size() < 2) {
    fail(input, "key needs key_id\\taction[\\tentity\\ttarget_page]");
    return false;
  }
  if (!micropad::parseAction(fields[1].c_str(), binding.action)) {
    fail(input, "unknown action '" + fields[1] + "'");
    return false;
  }
  if (fields.size() > 2) copyIdentifier(binding.entity, fields[2], "key.entity", input);
  if (fields.size() > 3) copyIdentifier(binding.targetPage, fields[3], "key.target_page", input);
  // The two gesture targets are optional and default to unbound (action none),
  // exactly like a keymap published before gestures existed.
  binding.hold = BindingTarget{};
  binding.doubleTap = BindingTarget{};
  if (fields.size() > 4 && !fields[4].empty() &&
      !micropad::parseAction(fields[4].c_str(), binding.hold.action)) {
    fail(input, "unknown hold action '" + fields[4] + "'");
    return false;
  }
  if (fields.size() > 5) copyIdentifier(binding.hold.entity, fields[5], "key.hold.entity", input);
  if (fields.size() > 6) {
    copyIdentifier(binding.hold.targetPage, fields[6], "key.hold.target_page", input);
  }
  if (fields.size() > 7 && !fields[7].empty() &&
      !micropad::parseAction(fields[7].c_str(), binding.doubleTap.action)) {
    fail(input, "unknown double action '" + fields[7] + "'");
    return false;
  }
  if (fields.size() > 8) {
    copyIdentifier(binding.doubleTap.entity, fields[8], "key.double.entity", input);
  }
  if (fields.size() > 9) {
    copyIdentifier(binding.doubleTap.targetPage, fields[9], "key.double.target_page", input);
  }
  return true;
}

bool readInput(Input &input, bool &havePage, bool &havePortalInfo) {
  std::string line;
  while (std::getline(std::cin, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    const std::string trimmed = trim(line);
    if (trimmed.empty() || trimmed[0] == '#') continue;
    const std::string keyword = directiveKeyword(trimmed);
    const std::string payload = directivePayload(trimmed);
    const std::vector<std::string> tokens = split(payload, ' ');

    if (keyword == "page") {
      const size_t idEnd = payload.find(' ');
      if (payload.empty() || idEnd == std::string::npos) {
        fail(input, "page needs <page_id> <title>");
        return false;
      }
      copyIdentifier(input.page.pageId, payload.substr(0, idEnd), "page_id", input);
      copyDisplay(input.page.title, payload.substr(idEnd + 1), "title", input);
      havePage = true;
    } else if (keyword == "state") {
      long selected = 0;
      long firstVisible = 0;
      bool editing = false;
      if (tokens.size() < 3 || !parseInt(tokens[0], selected) ||
          !parseInt(tokens[1], firstVisible) || !parseBool(tokens[2], editing)) {
        fail(input, "state needs <selected> <first_visible> <editing 0|1>");
        return false;
      }
      input.state.selected = static_cast<uint8_t>(selected < 0 ? 0 : selected);
      input.state.firstVisible = static_cast<uint8_t>(firstVisible < 0 ? 0 : firstVisible);
      input.state.editing = editing;
    } else if (keyword == "net") {
      long value = 0;
      if (tokens.size() < 1 || !parseInt(tokens[0], value) || value < 0 || value > 4) {
        fail(input, "net needs 0..4");
        return false;
      }
      input.snapshot.networkState = static_cast<uint8_t>(value);
    } else if (keyword == "usb") {
      if (tokens.size() < 1 || !parseBool(tokens[0], input.snapshot.usbHost)) {
        fail(input, "usb needs 0|1");
        return false;
      }
    } else if (keyword == "portal") {
      if (tokens.size() < 1 || !parseBool(tokens[0], input.portal)) {
        fail(input, "portal needs 0|1");
        return false;
      }
    } else if (keyword == "portalinfo") {
      const std::vector<std::string> fields = split(payload, '\t');
      if (fields.size() < 3) {
        fail(input, "portalinfo needs <ssid>\\t<password>\\t<address>");
        return false;
      }
      micropad::safeCopyClip(input.portalSsid, fields[0].c_str());
      micropad::safeCopyClip(input.portalPassword, fields[1].c_str());
      micropad::safeCopyClip(input.portalAddress, fields[2].c_str());
      havePortalInfo = true;
    } else if (keyword == "item") {
      if (!parseItem(payload, input)) return false;
    } else if (keyword == "key") {
      // `payload` already holds everything after "key".
      const std::vector<std::string> fields = split(payload, '\t');
      if (fields.size() < 2) {
        fail(input, "key needs key_id\\taction");
        return false;
      }
      KeyId key = KeyId::R0C0;
      const std::string keyId = trim(fields[0]);
      if (!micropad::parseKeyId(keyId.c_str(), key)) {
        fail(input, "unknown key id '" + keyId + "'");
        return false;
      }
      if (input.bindingCount >= MAX_BINDINGS) input.bindingCount = 0;
      // parseBinding reads field 1 as the action, i.e. it expects the key id to
      // stay in field 0 — pass the payload unchanged.
      if (!parseBinding(payload, input, input.bindings[input.bindingCount].binding)) {
        return false;
      }
      input.bindings[input.bindingCount].key = key;
      ++input.bindingCount;
    } else if (keyword == "press") {
      // The gesture is an optional second token; tabs are accepted as separators
      // like everywhere else in this format.
      std::string spaced = payload;
      for (char &character : spaced) {
        if (character == '\t') character = ' ';
      }
      const std::vector<std::string> parts = split(spaced, ' ');
      if (parts.size() < 1 || !micropad::parseKeyId(parts[0].c_str(), input.pressKey)) {
        fail(input, "press needs a known <key_id>");
        return false;
      }
      if (parts.size() > 1) {
        const std::string gesture = parts[1];
        if (gesture == "tap") {
          input.pressGesture = 0;
        } else if (gesture == "hold") {
          input.pressGesture = 1;
        } else if (gesture == "double") {
          input.pressGesture = 2;
        } else {
          fail(input, "press gesture must be tap, hold or double");
          return false;
        }
      }
      input.hasPress = true;
    } else {
      fail(input, "unknown directive '" + keyword + "'");
      return false;
    }
  }
  return true;
}

void resolvePress(Input &input) {
  if (!input.hasPress) return;
  for (size_t i = 0; i < input.bindingCount; ++i) {
    if (input.bindings[i].key == input.pressKey) {
      input.pressBinding = input.bindings[i].binding;
      input.hasBinding = true;
      return;
    }
  }
}

const char *primKindName(PrimKind kind) {
  switch (kind) {
    case PrimKind::Line: return "line";
    case PrimKind::Rect: return "rect";
    case PrimKind::FillRect: return "fillrect";
    case PrimKind::Circle: return "circle";
    case PrimKind::FillCircle: return "fillcircle";
    case PrimKind::FillTriangle: return "filltriangle";
    case PrimKind::Text: return "text";
  }
  return "unknown";
}

void printRows(const RenderSnapshot &snapshot) {
  std::printf("\"rows\":[");
  for (uint8_t i = 0; i < snapshot.rowCount; ++i) {
    const RenderRow &row = snapshot.rows[i];
    const char *style = row.style == RowStyle::Slider
                            ? "slider"
                            : (row.style == RowStyle::Checkbox ? "checkbox" : "text");
    std::printf(
        "%s{\"name\":\"%s\",\"state\":\"%s\",\"unit\":\"%s\",\"value\":%g,"
        "\"min\":%g,\"max\":%g,\"style\":\"%s\",\"selected\":%s,\"editing\":%s}",
        i == 0 ? "" : ",", jsonEscape(row.name).c_str(), jsonEscape(row.state).c_str(),
        jsonEscape(row.unit).c_str(), static_cast<double>(row.value),
        static_cast<double>(row.min), static_cast<double>(row.max), style,
        row.selected ? "true" : "false", row.editing ? "true" : "false");
  }
  std::printf("],");
}

void printPrims(const RenderModel &model) {
  std::printf("\"prims\":[");
  for (uint16_t i = 0; i < model.count; ++i) {
    const RenderPrim &prim = model.prims[i];
    std::printf(
        "%s{\"kind\":\"%s\",\"x0\":%d,\"y0\":%d,\"x1\":%d,\"y1\":%d,\"w\":%d,"
        "\"h\":%d,\"radius\":%u,\"color\":%u,\"align\":\"%s\",\"text\":\"%s\"}",
        i == 0 ? "" : ",", primKindName(prim.kind), prim.x0, prim.y0, prim.x1, prim.y1,
        prim.w, prim.h, prim.radius, prim.color,
        prim.align == static_cast<uint8_t>(TextAlign::Right) ? "right" : "left",
        jsonEscape(prim.text).c_str());
  }
  std::printf("],");
}

void printFindings(const Input &input) {
  std::printf("\"findings\":[");
  for (size_t i = 0; i < input.findings.size(); ++i) {
    const Finding &finding = input.findings[i];
    std::printf(
        "%s{\"code\":\"%s\",\"severity\":\"%s\",\"field\":\"%s\",\"cap\":%zu,"
        "\"chars\":%zu}",
        i == 0 ? "" : ",", finding.code, finding.severity,
        jsonEscape(finding.field.c_str()).c_str(), finding.cap, finding.chars);
  }
  std::printf("],");
}

void printPress(const Input &input) {
  if (!input.hasPress) {
    std::printf("\"press\":null");
    return;
  }
  // The gesture the caller asked about resolves exactly like the firmware does:
  // a bound hold/double wins over the tap, an unbound one falls back to it.
  const bool longPress = input.pressGesture == 1;
  const bool doublePress = input.pressGesture == 2;
  micropad::Binding effective{};
  if (input.hasBinding) {
    InputEvent scoped{};
    scoped.key = input.pressKey;
    scoped.pressed = true;
    scoped.longPress = longPress;
    scoped.doublePress = doublePress;
    micropad::effectiveBinding(effective, input.pressBinding, scoped);
  }
  const bool gestureBound =
      input.hasBinding &&
      ((longPress && input.pressBinding.hold.action != Action::None) ||
       (doublePress && input.pressBinding.doubleTap.action != Action::None));
  const char *bindingAction =
      input.hasBinding ? micropad::actionName(gestureBound ? effective.action
                                                          : input.pressBinding.action)
                       : nullptr;
  const char *itemAction = nullptr;
  if (!input.portal && input.state.selected < input.page.itemCount) {
    itemAction = micropad::actionName(micropad::actionForSelectedItem(
        input.page.items[input.state.selected], input.state.editing));
  }
  std::printf(
      "\"press\":{\"key\":\"%s\",\"gesture\":\"%s\",\"binding_action\":\"%s\","
      "\"item_action\":\"%s\",\"entity\":\"%s\",\"target_page\":\"%s\"}",
      micropad::keyIdName(input.pressKey),
      longPress ? "hold" : (doublePress ? "double" : "tap"),
      bindingAction == nullptr ? "" : bindingAction,
      itemAction == nullptr ? "" : itemAction,
      jsonEscape(gestureBound ? effective.entity : input.pressBinding.entity).c_str(),
      jsonEscape(gestureBound ? effective.targetPage : input.pressBinding.targetPage).c_str());
}

int render(Input &input) {
  RenderModel model{};
  const char *mode = "normal";
  if (input.portal) {
    mode = "portal";
    input.snapshot.portal = true;
    micropad::safeCopyClip(input.snapshot.portalSsid, input.portalSsid);
    micropad::safeCopyClip(input.snapshot.portalPassword, input.portalPassword);
    micropad::safeCopyClip(input.snapshot.portalAddress, input.portalAddress);
    input.snapshot.rowCount = 0;
    input.snapshot.totalItems = 0;
    input.snapshot.title[0] = '\0';
    micropad::layoutPortalUi(input.snapshot, model);
  } else {
    micropad::normalizeSelection(&input.page, input.state);
    micropad::fillPageSnapshot(input.page, input.state, input.snapshot);
    micropad::layoutNormalUi(input.snapshot, model);
  }

  std::printf(
      "{\"ok\":true,\"firmware\":\"%s\",\"mode\":\"%s\",\"title\":\"%s\","
      "\"total_items\":%u,\"first_visible\":%u,\"row_count\":%u,",
      micropad::FIRMWARE_VERSION, mode, jsonEscape(input.snapshot.title).c_str(),
      input.snapshot.totalItems, input.snapshot.firstVisible, input.snapshot.rowCount);
  printRows(input.snapshot);
  printPrims(model);
  std::printf("\"prims_truncated\":%s,\"would_reject\":%s,",
              model.count >= micropad::RENDER_PRIM_CAP ? "true" : "false",
              input.wouldReject ? "true" : "false");
  printFindings(input);
  printPress(input);
  std::printf("}\n");
  return 0;
}

int usage() {
  std::fprintf(stderr,
               "usage: micropad_sim < payload.txt   (see the header of "
               "tools/micropad_sim.cpp for the directive format)\n");
  return 3;
}

}  // namespace

int main(int argc, char **argv) {
  for (int i = 1; i < argc; ++i) {
    if (std::strcmp(argv[i], "--help") == 0) return usage();
    if (std::strcmp(argv[i], "--version") == 0) {
      std::printf("%s\n", micropad::FIRMWARE_VERSION);
      return 0;
    }
    return usage();
  }
  Input input;
  input.snapshot.generation = 1;
  bool havePage = false;
  bool havePortalInfo = false;
  const bool read = readInput(input, havePage, havePortalInfo);
  // The portal view is what the pad shows *instead of* a page, so it needs no
  // page directive; every other mode does.
  if (read && !havePage && !input.portal) fail(input, "missing page directive");
  if (read && input.portal && !havePortalInfo) fail(input, "portal 1 needs portalinfo");
  if (input.malformed) {
    std::printf("{\"ok\":false,\"error\":\"%s\"}\n",
                jsonEscape(input.malformedReason.c_str()).c_str());
    return 2;
  }
  resolvePress(input);
  return render(input);
}
