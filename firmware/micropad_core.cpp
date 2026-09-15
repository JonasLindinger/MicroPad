// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
#include "micropad_core.h"

#include <cstdio>

namespace micropad {

namespace {

// String tables ordered identically to their enums. A token's index in the
// table is its enum value.
constexpr const char *kKeyNames[KEY_COUNT] = {
    "r0c0", "r0c1", "r0c2", "r0c3", "r1c0", "r1c1", "r1c2",
    "r1c3", "r2c0", "r2c1", "r2c2", "r2c3", "enc_up", "enc_down"};

constexpr const char *kActionNames[ACTION_COUNT] = {
    "none",          "enter",      "back",          "home",
    "settings",      "scroll",     "scroll_up",     "scroll_down",
    "navigate",      "keymap",     "get_all_pages", "toggle",
    "on",            "off",        "press",         "volume_up",
    "volume_down",   "media_next", "media_prev",    "edit",
    "confirm"};

constexpr const char *kItemTypeNames[ITEM_TYPE_COUNT] = {
    "category", "light", "switch", "script", "button", "scene",
    "sensor",   "media_player", "number", "settings", "back"};

// Generic scalar lookup helpers over a constexpr name table.
template <size_t N>
const char *nameFor(const char *const (&table)[N], size_t count,
                    size_t index) {
  if (index >= count) return nullptr;
  return table[index];
}

template <size_t N>
bool valueFor(const char *const (&table)[N], size_t count, const char *text,
              size_t &outIndex) {
  if (text == nullptr) return false;
  for (size_t i = 0; i < count; ++i) {
    if (std::strcmp(table[i], text) == 0) {
      outIndex = i;
      return true;
    }
  }
  return false;
}

// Quarter-step contribution for each (previousAB << 2) | currentAB transition.
// Impossible two-bit jumps (both bits change) contribute 0 and are additionally
// cleared by quadrature update.
constexpr int8_t kQuadratureSteps[16] = {
    // prev 00
    0, 1, -1, 0,
    // prev 01
    -1, 0, 0, 1,
    // prev 10
    1, 0, 0, -1,
    // prev 11
    0, -1, 1, 0};

}  // namespace

// Behaviour per item type. Rows are in ItemType order (a host test asserts that)
// so itemTypeDescriptor() can index directly; the static tests assert these
// defaultAction/editable values match contracts/mqtt-contract.json
// (item_type_meta), which is what the configurator renders.
const ItemTypeDescriptor ITEM_TYPE_DESCRIPTORS[ITEM_TYPE_COUNT] = {
    {ItemType::Category, Action::Navigate, false},
    {ItemType::Light, Action::Toggle, false},
    {ItemType::Switch, Action::Toggle, false},
    {ItemType::Script, Action::Press, false},
    {ItemType::Button, Action::Press, false},
    {ItemType::Scene, Action::Press, false},
    {ItemType::Sensor, Action::None, false},
    {ItemType::MediaPlayer, Action::None, false},
    {ItemType::Number, Action::Edit, true},
    {ItemType::Settings, Action::Settings, false},
    {ItemType::Back, Action::Back, false},
};

const ItemTypeDescriptor &itemTypeDescriptor(ItemType type) {
  const size_t index = static_cast<size_t>(type);
  // Defensive: an out-of-range type (corrupt payload) must not read past the
  // table; Category is a harmless inert default.
  return ITEM_TYPE_DESCRIPTORS[index < ITEM_TYPE_COUNT ? index : 0];
}

const char *keyIdName(KeyId key) {
  return nameFor(kKeyNames, KEY_COUNT, static_cast<size_t>(key));
}

bool parseKeyId(const char *text, KeyId &out) {
  size_t index = 0;
  if (!valueFor(kKeyNames, KEY_COUNT, text, index)) return false;
  out = static_cast<KeyId>(index);
  return true;
}

const char *actionName(Action action) {
  return nameFor(kActionNames, ACTION_COUNT, static_cast<size_t>(action));
}

bool parseAction(const char *text, Action &out) {
  size_t index = 0;
  if (!valueFor(kActionNames, ACTION_COUNT, text, index)) return false;
  out = static_cast<Action>(index);
  return true;
}

bool parseItemType(const char *text, ItemType &out) {
  size_t index = 0;
  if (!valueFor(kItemTypeNames, ITEM_TYPE_COUNT, text, index)) return false;
  out = static_cast<ItemType>(index);
  return true;
}

void loadDefaultKeymap(Binding (&out)[KEY_COUNT]) {
  for (size_t i = 0; i < KEY_COUNT; ++i) {
    out[i].action = Action::None;
    safeCopy(out[i].entity, "");
    safeCopy(out[i].targetPage, "");
  }
  out[0].action = Action::Home;       // r0c0
  out[3].action = Action::Enter;      // r0c3
  out[7].action = Action::Enter;      // r1c3
  out[11].action = Action::Back;      // r2c3
  out[12].action = Action::ScrollUp;  // enc_up
  out[13].action = Action::ScrollDown;  // enc_down
}

Action resolveBinding(const Binding &binding, const InputEvent &input) {
  if (!input.pressed) return Action::None;
  if (binding.action != Action::Scroll) return binding.action;
  if (input.direction < 0) return Action::ScrollUp;
  if (input.direction > 0) return Action::ScrollDown;
  return Action::None;
}

namespace {

bool nonEmpty(const char *text) {
  return text != nullptr && text[0] != '\0';
}

bool lengthInRange(const char *text, size_t lo, size_t hi) {
  if (text == nullptr) return false;
  const size_t length = std::strlen(text);
  return length >= lo && length <= hi;
}

}  // namespace

int8_t SnapshotMailbox::renderedSlot() const {
  for (int8_t i = 0; i < SLOT_COUNT; ++i) {
    if (slots_[i].state == SlotState::Rendering) return i;
  }
  return -1;
}

int8_t SnapshotMailbox::beginWrite() {
  // The non-rendering slot is the only one Core 1 may touch. A still-published
  // slot is demoted to Free so the slot can be rewritten in place; the newest
  // generation on the wire is always the one about to be published.
  const int8_t rendering = renderedSlot();
  for (int8_t i = 0; i < SLOT_COUNT; ++i) {
    if (i == rendering) continue;
    if (slots_[i].state == SlotState::Published) {
      if (newestIndex_ == i) newestIndex_ = -1;
      slots_[i].state = SlotState::Free;
    }
    if (slots_[i].state == SlotState::Free) {
      slots_[i].state = SlotState::Writing;
      return i;
    }
  }
  return -1;
}

RenderSnapshot &SnapshotMailbox::writable(int8_t slot) {
  return slots_[slot].snapshot;
}

void SnapshotMailbox::publish(int8_t slot) {
  // The snapshot bytes were filled outside the critical section and are only
  // marked visible here; publication never mutates them. Any older published
  // slot is demoted so at most one Published slot exists, and the generation
  // counter advances so the renderer can order claims.
  for (int8_t i = 0; i < SLOT_COUNT; ++i) {
    if (i != slot && slots_[i].state == SlotState::Published) {
      if (newestIndex_ == i) newestIndex_ = -1;
      slots_[i].state = SlotState::Free;
    }
  }
  slots_[slot].state = SlotState::Published;
  newestIndex_ = slot;
  ++generation_;
}

int8_t SnapshotMailbox::claimNewest() {
  // Exactly one slot is Published at any moment; the newest index is recorded
  // at publish time. The claim flips it to Rendering in the same critical
  // section so Core 1 can only begin writing the other slot from here on.
  if (newestIndex_ >= 0 &&
      slots_[newestIndex_].state == SlotState::Published) {
    slots_[newestIndex_].state = SlotState::Rendering;
    return newestIndex_;
  }
  for (int8_t i = 0; i < SLOT_COUNT; ++i) {
    if (slots_[i].state == SlotState::Published) {
      slots_[i].state = SlotState::Rendering;
      newestIndex_ = i;
      return i;
    }
  }
  return -1;
}

const RenderSnapshot &SnapshotMailbox::readable(int8_t slot) const {
  return slots_[slot].snapshot;
}

void SnapshotMailbox::release(int8_t slot) {
  if (slot < 0 || slot >= SLOT_COUNT ||
      slots_[slot].state != SlotState::Rendering) {
    return;
  }
  slots_[slot].state = SlotState::Free;
  if (newestIndex_ == slot) newestIndex_ = -1;
}

bool SnapshotMailbox::hasPending() const {
  for (int8_t i = 0; i < SLOT_COUNT; ++i) {
    const SlotState state = slots_[i].state;
    if (state == SlotState::Writing || state == SlotState::Published) {
      return true;
    }
  }
  return false;
}

Settings defaultSettings() {
  Settings value{};
  safeCopy(value.mqttHost, "192.168.0.100");  // placeholder, never real
  value.mqttPort = 1883;
  safeCopy(value.mqttUser, "micropad");        // placeholder
  safeCopy(value.mqttPassword, "replace-me");  // placeholder
  return value;
}

bool validateSettings(const Settings &settings) {
  if (settings.mqttPort < 1) return false;  // uint16_t max is 65535
  if (!nonEmpty(settings.mqttHost)) return false;
  if (!nonEmpty(settings.mqttUser)) return false;
  // Empty SSID = first-boot portal; any captured password is ignored. A
  // nonempty SSID requires a Wi-Fi password of 8..64 characters.
  if (settings.wifiSsid[0] == '\0') return true;
  return lengthInRange(settings.wifiPassword, 8, WIFI_PASS_CAP - 1);
}

// Wrap-safe unsigned elapsed time. nowMs may wrap past UINT32_MAX; subtracting
// two uint32_t values still yields the correct cyclic distance.
static constexpr bool stableFor(uint32_t nowMs, uint32_t sinceMs) {
  return static_cast<uint32_t>(nowMs - sinceMs) >= INPUT_DEBOUNCE_MS;
}

Edge DebouncedInput::update(bool rawPressed, uint32_t nowMs) {
  if (rawPressed != candidate_) {
    candidate_ = rawPressed;
    candidateSinceMs_ = nowMs;
    return Edge::None;
  }
  if (candidate_ == stable_) return Edge::None;
  if (!stableFor(nowMs, candidateSinceMs_)) return Edge::None;
  stable_ = candidate_;
  return stable_ ? Edge::Pressed : Edge::Released;
}

void DebouncedInput::primeForWake(bool rawPressed, uint32_t nowMs) {
  // Wake-time re-prime (warm-sleep task): collapse candidate and stable to
  // the raw level. A level that is already true at wake is the press that
  // woke the device — the sketch's immediate wake scan already delivered it,
  // so the first ordinary scans must not re-emit it, and its eventual release
  // still produces a Released edge. A raw level that differs from the primed
  // level debounces from nowMs rather than being accepted silently as a new
  // baseline.
  candidate_ = rawPressed;
  stable_ = rawPressed;
  candidateSinceMs_ = nowMs;
}

bool DebouncedInput::held() const { return stable_; }

PowerEdge PowerDebouncer::sample(bool rawConnected, uint32_t nowMs) {
  // The very first sample only seeds the baseline; a combined stable/raw
  // disagreement can never emit an edge out of an unknown boot state.
  if (!initialized_) {
    candidate_ = rawConnected;
    candidateSinceMs_ = nowMs;
    initialized_ = true;
    return PowerEdge::None;
  }
  // A raw change restarts the candidate window and never emits immediately.
  if (rawConnected != candidate_) {
    candidate_ = rawConnected;
    candidateSinceMs_ = nowMs;
    return PowerEdge::None;
  }
  // Repeated samples of the accepted state (and of a candidate still inside
  // its window) emit nothing.
  if (candidate_ == stable_) return PowerEdge::None;
  if (elapsedMs(nowMs, candidateSinceMs_) < USB_STABLE_MS) {
    return PowerEdge::None;
  }
  // The same candidate held for USB_STABLE_MS: exactly one edge, then the
  // state is accepted until the raw level changes and stabilizes again.
  stable_ = candidate_;
  return stable_ ? PowerEdge::Connected : PowerEdge::Disconnected;
}

bool canSleep(bool powered, bool keyHeld, bool renderBusy,
              bool renderPending, uint32_t nowMs, uint32_t lastActivityMs,
              uint32_t awakeStartedMs) {
  if (powered) return false;
  if (keyHeld) return false;
  if (renderBusy) return false;
  if (renderPending) return false;
  if (elapsedMs(nowMs, lastActivityMs) < IDLE_SLEEP_MS) return false;
  if (elapsedMs(nowMs, awakeStartedMs) < MIN_AWAKE_MS) return false;
  return true;
}

void QuadratureDecoder::reset(uint8_t ab) {
  past_ = static_cast<uint8_t>(ab & 0x03);
  accum_ = 0;
}

int8_t QuadratureDecoder::update(uint8_t ab) {
  const uint8_t current = static_cast<uint8_t>(ab & 0x03);
  const uint8_t index = static_cast<uint8_t>((past_ << 2) | current);
  int8_t result = 0;
  if (((past_ ^ current) & 0x03) == 0x03) {
    accum_ = 0;  // impossible two-bit jump: drop partial accumulation
  } else {
    accum_ = static_cast<int8_t>(accum_ + kQuadratureSteps[index]);
    if (accum_ >= 2) {
      accum_ = 0;
      result = 1;
    } else if (accum_ <= -2) {
      accum_ = 0;
      result = -1;
    }
  }
  past_ = current;
  return result;
}

void scanMatrixPass(DebouncedInput (&debounce)[MATRIX_KEY_COUNT],
                    MatrixReadFn readKey, EventEmitFn emit, uint32_t nowMs) {
  if (readKey == nullptr || emit == nullptr) return;
  for (uint8_t row = 0; row < MATRIX_ROWS; ++row) {
    for (uint8_t col = 0; col < MATRIX_COLS; ++col) {
      const uint8_t index = matrixKeyIndex(row, col);
      const Edge edge = debounce[index].update(readKey(row, col), nowMs);
      if (edge != Edge::None) {
        emit(InputEvent{static_cast<KeyId>(index), edge == Edge::Pressed, 0,
                        nowMs});
      }
    }
  }
}

void scanEncoderPass(QuadratureDecoder &decoder, uint8_t phase,
                     EventEmitFn emit, uint32_t nowMs) {
  if (emit == nullptr) return;
  const int8_t step = decoder.update(phase);
  if (step != 0) emit(encoderEvent(step, nowMs));
}

bool anyMatrixKeyHeld(const DebouncedInput (&debounce)[MATRIX_KEY_COUNT],
                      MatrixReadFn readKey) {
  for (size_t index = 0; index < MATRIX_KEY_COUNT; ++index) {
    if (debounce[index].held()) return true;
  }
  if (readKey == nullptr) return false;
  for (uint8_t row = 0; row < MATRIX_ROWS; ++row) {
    bool pressed = false;
    for (uint8_t col = 0; col < MATRIX_COLS; ++col) {
      if (readKey(row, col)) pressed = true;
    }
    if (pressed) return true;
  }
  return false;
}

bool EventQueue::isReplaceable(const Event &e) {
  return e.action == Action::ScrollUp || e.action == Action::ScrollDown ||
         e.action == Action::Edit;
}

bool EventQueue::matches(const Event &a, const Event &b) {
  return a.action == b.action && std::strcmp(a.entity, b.entity) == 0 &&
         std::strcmp(a.targetPage, b.targetPage) == 0;
}

bool EventQueue::push(const Event &event) {
  if (size_ < QUEUE_CAPACITY) {
    slots_[(head_ + size_) % QUEUE_CAPACITY] = event;
    ++size_;
    return true;
  }
  // Full queue: a replaceable event coalesces with a matching replaceable
  // slot; otherwise the oldest replaceable slot is evicted to make room. Only
  // when no slot can be coalesced or evicted is the push rejected.
  if (isReplaceable(event) && coalesce(event)) return true;
  if (evictOldestReplaceable()) {
    slots_[(head_ + size_) % QUEUE_CAPACITY] = event;
    ++size_;
    return true;
  }
  ++overflow_;
  return false;
}

bool EventQueue::pushFront(const Event &event) {
  if (size_ >= QUEUE_CAPACITY) return false;
  head_ = (head_ + QUEUE_CAPACITY - 1) % QUEUE_CAPACITY;
  slots_[head_] = event;
  ++size_;
  return true;
}

bool EventQueue::pop(Event &out) {
  if (size_ == 0) return false;
  out = slots_[head_];
  head_ = (head_ + 1) % QUEUE_CAPACITY;
  --size_;
  return true;
}

bool EventQueue::coalesce(const Event &event) {
  // Newest-to-oldest scan for a matching replaceable slot; overwrite in place,
  // leaving size unchanged.
  for (size_t k = size_; k-- > 0;) {
    const size_t idx = (head_ + k) % QUEUE_CAPACITY;
    if (isReplaceable(slots_[idx]) && matches(slots_[idx], event)) {
      slots_[idx] = event;
      return true;
    }
  }
  return false;
}

bool EventQueue::evictOldestReplaceable() {
  // Oldest-to-newest: remove the first replaceable slot, shifting every entry
  // after it one slot toward head and shrinking size by one. At most
  // QUEUE_CAPACITY - 1 entries are shifted.
  for (size_t k = 0; k < size_; ++k) {
    const size_t idx = (head_ + k) % QUEUE_CAPACITY;
    if (!isReplaceable(slots_[idx])) continue;
    for (size_t m = k; m + 1 < size_; ++m) {
      const size_t to = (head_ + m) % QUEUE_CAPACITY;
      const size_t from = (head_ + m + 1) % QUEUE_CAPACITY;
      slots_[to] = slots_[from];
    }
    --size_;
    return true;
  }
  return false;
}

size_t flushEvents(EventQueue &queue, EventDispatchFn dispatch, size_t budget) {
  size_t n = 0;
  while (dispatch != nullptr && n < budget) {
    Event e;
    if (!queue.pop(e)) break;
    dispatch(e);
    ++n;
  }
  return n;
}

Page *findPage(Catalog &catalog, const char *pageId) {
  if (pageId == nullptr) return nullptr;
  for (uint8_t i = 0; i < catalog.pageCount && i < MAX_PAGES; ++i) {
    if (std::strcmp(catalog.pages[i].pageId, pageId) == 0) {
      return &catalog.pages[i];
    }
  }
  return nullptr;
}

const Page *findPage(const Catalog &catalog, const char *pageId) {
  if (pageId == nullptr) return nullptr;
  for (uint8_t i = 0; i < catalog.pageCount && i < MAX_PAGES; ++i) {
    if (std::strcmp(catalog.pages[i].pageId, pageId) == 0) {
      return &catalog.pages[i];
    }
  }
  return nullptr;
}

bool commitCurrentPage(PadController &pad, const Page &page, uint32_t nowMs) {
  if (page.pageId[0] == '\0') return false;
  if (page.itemCount > MAX_ITEMS_PER_PAGE) return false;
  Page *slot = findPage(pad.catalog, page.pageId);
  if (slot == nullptr) {
    if (pad.catalog.pageCount >= MAX_PAGES) return false;
    slot = &pad.catalog.pages[pad.catalog.pageCount];
    ++pad.catalog.pageCount;
  }
  *slot = page;  // complete replacement, never a partial merge
  if (std::strcmp(pad.state.pageId, page.pageId) == 0) {
    // The authoritative data for the page on screen arrived: clamp selection
    // only now, leave edit mode, and treat a pending quiet-period resync as
    // fulfilled.
    normalizeSelection(slot, pad.state);
    pad.state.editing = false;
    pad.state.resyncPending = false;
    pad.state.lastInputMs = nowMs;
  }
  return true;
}

bool pageContentEqual(const Page &a, const Page &b) {
  if (std::strcmp(a.pageId, b.pageId) != 0 ||
      std::strcmp(a.title, b.title) != 0 ||
      std::strcmp(a.parent, b.parent) != 0 || a.itemCount != b.itemCount) {
    return false;
  }
  for (uint8_t i = 0; i < a.itemCount; ++i) {
    const Item &x = a.items[i];
    const Item &y = b.items[i];
    if (std::strcmp(x.name, y.name) != 0 || x.type != y.type ||
        std::strcmp(x.entity, y.entity) != 0 ||
        std::strcmp(x.state, y.state) != 0 || x.value != y.value ||
        x.min != y.min || x.max != y.max || x.step != y.step ||
        std::strcmp(x.unit, y.unit) != 0 || x.editable != y.editable ||
        std::strcmp(x.targetPage, y.targetPage) != 0) {
      return false;
    }
  }
  return true;
}

void replaceKeymap(Binding (&dst)[KEY_COUNT], const Binding (&src)[KEY_COUNT]) {
  std::memcpy(dst, src, sizeof(dst));
}

void normalizeSelection(const Page *page, AppState &state) {
  const uint8_t count = page != nullptr ? page->itemCount : 0;
  if (count == 0) {
    state.selected = 0;
    state.firstVisible = 0;
    return;
  }
  if (state.selected >= count) {
    state.selected = static_cast<uint8_t>(count - 1);
  }
  if (state.selected < state.firstVisible) state.firstVisible = state.selected;
  const uint8_t window = static_cast<uint8_t>(VISIBLE_ROWS);
  const uint8_t windowLast =
      static_cast<uint8_t>(state.firstVisible + window - 1);
  if (windowLast >= count) {
    // The window overruns the page: bottom-align it when the page is taller
    // than the window, else pin it to the top.
    state.firstVisible = count <= window ? 0 : static_cast<uint8_t>(count - window);
  } else if (state.selected >= state.firstVisible + window) {
    state.firstVisible = static_cast<uint8_t>(
        state.selected >= window ? state.selected - (window - 1) : 0);
  }
}

Action actionForSelectedItem(const Item &item, bool editing) {
  const ItemTypeDescriptor &descriptor = itemTypeDescriptor(item.type);
  // Edit mode turns the confirming press of an editable type into Confirm; every
  // other combination is the table's default action for the type.
  if (editing && descriptor.editable) return Action::Confirm;
  return descriptor.defaultAction;
}

namespace {

void beginPage(PadController &pad, const Page *dest) {
  safeCopy(pad.state.pageId, dest->pageId);
  pad.state.selected = 0;
  pad.state.firstVisible = 0;
  pad.state.editing = false;
}

}  // namespace

Page *PadController::currentPage() { return findPage(catalog, state.pageId); }

Item *PadController::currentItem() {
  Page *page = currentPage();
  if (page == nullptr || state.selected >= page->itemCount) return nullptr;
  return &page->items[state.selected];
}

Action PadController::selectedItemAction() const {
  const Page *page = findPage(catalog, state.pageId);
  if (page == nullptr || state.selected >= page->itemCount) return Action::None;
  return actionForSelectedItem(page->items[state.selected], state.editing);
}

bool PadController::resyncDue(uint32_t nowMs) const {
  return state.resyncPending &&
         static_cast<uint32_t>(nowMs - state.lastInputMs) >= RESYNC_SETTLE_MS;
}

void PadController::noteInputBurst(uint32_t atMs) { state.lastInputMs = atMs; }

ApplyResult PadController::applyAction(Action action, const Binding &binding,
                                       uint32_t atMs) {
  ApplyResult result{false, false};
  bool changed = false;
  bool wantEvent = false;
  Event out{};
  out.action = action;
  safeCopy(out.entity, binding.entity);
  safeCopy(out.pageId, state.pageId);  // origin page for event.page_id
  safeCopy(out.targetPage, binding.targetPage);

  Page *page = currentPage();
  Item *item = currentItem();

  switch (action) {
    case Action::ScrollUp:
    case Action::ScrollDown: {
      if (state.editing && item != nullptr && item->type == ItemType::Number) {
        // Optimistic number edit: adjust by the item's positive step, clamped
        // to [min, max], and queue an Edit carrying the new value.
        const float step = item->step > 0.0f ? item->step : 1.0f;
        float value = item->value;
        if (action == Action::ScrollDown) {
          if (item->value + step <= item->max) {
            value = item->value + step;
          } else if (item->value < item->max) {
            value = item->max;
          } else {
            break;
          }
        } else {
          if (item->value - step >= item->min) {
            value = item->value - step;
          } else if (item->value > item->min) {
            value = item->min;
          } else {
            break;
          }
        }
        item->value = value;
        changed = true;
        wantEvent = true;
        out.action = Action::Edit;
        out.value = item->value;
        safeCopy(out.entity, item->entity);
        safeCopy(out.targetPage, "");
        break;
      }
      if (page == nullptr) break;
      if (action == Action::ScrollDown) {
        if (state.selected + 1 >= page->itemCount) break;
        ++state.selected;
      } else {
        if (state.selected == 0) break;
        --state.selected;
      }
      normalizeSelection(page, state);
      changed = true;
      wantEvent = true;
      safeCopy(out.targetPage, state.pageId);
      break;
    }
    case Action::Toggle: {
      // P1.1: an entity action addresses exactly the binding's entity — the
      // currently selected item is never substituted for it. A missing entity
      // is a defined no-op; the backend and UI reject it visibly instead.
      if (binding.entity[0] == '\0') break;
      safeCopy(out.entity, binding.entity);
      wantEvent = true;
      if (item != nullptr && std::strcmp(item->entity, binding.entity) == 0) {
        const bool isOn = std::strcmp(item->state, "on") == 0;
        safeCopy(item->state, isOn ? "off" : "on");
        changed = true;
      }
      break;
    }
    case Action::On:
    case Action::Off: {
      if (binding.entity[0] == '\0') break;
      safeCopy(out.entity, binding.entity);
      wantEvent = true;
      if (item != nullptr && std::strcmp(item->entity, binding.entity) == 0) {
        safeCopy(item->state, action == Action::On ? "on" : "off");
        changed = true;
      }
      break;
    }
    case Action::Press: {
      if (binding.entity[0] == '\0') break;
      safeCopy(out.entity, binding.entity);
      wantEvent = true;
      break;
    }
    case Action::VolumeUp:
    case Action::VolumeDown:
    case Action::MediaNext:
    case Action::MediaPrev: {
      // Documented media-control events address the bound entity; local state
      // stays unchanged.
      if (binding.entity[0] == '\0') break;
      safeCopy(out.entity, binding.entity);
      wantEvent = true;
      break;
    }
    case Action::Keymap: {
      // A keymap request event: the backend republishes the effective map for
      // the requested page. A binding target page requests that page's map;
      // otherwise the current page is the origin (P1.1: target_page honoured).
      wantEvent = true;
      if (binding.targetPage[0] != '\0') {
        const Page *dest = findPage(catalog, binding.targetPage);
        safeCopy(out.targetPage,
                 dest != nullptr ? dest->pageId : binding.targetPage);
      }
      break;
    }
    case Action::Edit: {
      // Editing drives local edit state, so it applies to the selected number
      // item — and only while that item is the bound entity.
      if (binding.entity[0] == '\0' || item == nullptr ||
          item->type != ItemType::Number ||
          std::strcmp(item->entity, binding.entity) != 0) {
        break;
      }
      state.editing = true;
      changed = true;
      wantEvent = true;
      safeCopy(out.entity, binding.entity);
      out.value = item->value;
      break;
    }
    case Action::Confirm: {
      if (item == nullptr || !state.editing ||
          item->type != ItemType::Number) {
        break;
      }
      state.editing = false;
      changed = true;
      wantEvent = true;
      out.action = Action::Edit;
      // The confirmed item is the target; an Enter-resolved binding carries the
      // same entity, so both agree (P1.1).
      safeCopy(out.entity,
               binding.entity[0] != '\0' ? binding.entity : item->entity);
      out.value = item->value;
      break;
    }
    case Action::Navigate: {
      const char *target = (item != nullptr && item->targetPage[0] != '\0')
                               ? item->targetPage
                               : binding.targetPage;
      if (target[0] == '\0') break;
      Page *dest = findPage(catalog, target);
      if (dest == nullptr) break;
      beginPage(*this, dest);
      changed = true;
      wantEvent = true;
      safeCopy(out.targetPage, dest->pageId);
      break;
    }
    case Action::Home: {
      Page *dest = findPage(catalog, "home");
      if (dest == nullptr) break;
      beginPage(*this, dest);
      changed = true;
      wantEvent = true;
      safeCopy(out.targetPage, "home");
      break;
    }
    case Action::Back: {
      if (page == nullptr || page->parent[0] == '\0') break;
      Page *dest = findPage(catalog, page->parent);
      if (dest == nullptr) break;
      beginPage(*this, dest);
      changed = true;
      wantEvent = true;
      safeCopy(out.targetPage, dest->pageId);
      break;
    }
    case Action::Settings: {
      Page *dest = findPage(catalog, "settings");
      if (dest == nullptr) break;
      beginPage(*this, dest);
      changed = true;
      wantEvent = true;
      safeCopy(out.targetPage, dest->pageId);
      break;
    }
    default:
      break;  // None, Enter (resolved upstream), media/volume, keymap, etc.
  }

  result.stateChanged = changed;
  if (wantEvent) result.eventReady = queue.push(out);
  if (changed || wantEvent) {
    state.lastInputMs = atMs;
    state.resyncPending = true;
  }
  return result;
}

// ============================================================================
// Task 9: landscape UI geometry and refresh helpers. These builders emit a
// deterministic prim model (lines, rects, circles, triangles, text spans)
// that the Arduino sketch translates into GxEPD2 draw calls inside
// drawSnapshot(); the model is the host-testable, text-representable contract
// for the full-panel landscape UI.
// ============================================================================

void addLine(RenderModel &model, int16_t x0, int16_t y0, int16_t x1,
             int16_t y1) {
  if (model.count >= RENDER_PRIM_CAP) return;
  RenderPrim &prim = model.prims[model.count++];
  prim = RenderPrim{};
  prim.kind = PrimKind::Line;
  prim.x0 = x0;
  prim.y0 = y0;
  prim.x1 = x1;
  prim.y1 = y1;
}

void addRect(RenderModel &model, int16_t x, int16_t y, int16_t w, int16_t h,
             uint8_t color) {
  if (model.count >= RENDER_PRIM_CAP) return;
  RenderPrim &prim = model.prims[model.count++];
  prim = RenderPrim{};
  prim.kind = PrimKind::Rect;
  prim.x0 = x;
  prim.y0 = y;
  prim.w = w;
  prim.h = h;
  prim.color = color;
}

void addFillRect(RenderModel &model, int16_t x, int16_t y, int16_t w,
                 int16_t h, uint8_t color) {
  if (model.count >= RENDER_PRIM_CAP) return;
  RenderPrim &prim = model.prims[model.count++];
  prim = RenderPrim{};
  prim.kind = PrimKind::FillRect;
  prim.x0 = x;
  prim.y0 = y;
  prim.w = w;
  prim.h = h;
  prim.color = color;
}

void addCircle(RenderModel &model, int16_t cx, int16_t cy, uint8_t radius,
               uint8_t color) {
  if (model.count >= RENDER_PRIM_CAP) return;
  RenderPrim &prim = model.prims[model.count++];
  prim = RenderPrim{};
  prim.kind = PrimKind::Circle;
  prim.x0 = cx;
  prim.y0 = cy;
  prim.radius = radius;
  prim.color = color;
}

void addFillCircle(RenderModel &model, int16_t cx, int16_t cy,
                   uint8_t radius, uint8_t color) {
  if (model.count >= RENDER_PRIM_CAP) return;
  RenderPrim &prim = model.prims[model.count++];
  prim = RenderPrim{};
  prim.kind = PrimKind::FillCircle;
  prim.x0 = cx;
  prim.y0 = cy;
  prim.radius = radius;
  prim.color = color;
}

void addFillTriangle(RenderModel &model, int16_t x0, int16_t y0, int16_t x1,
                     int16_t y1, int16_t x2, int16_t y2) {
  if (model.count >= RENDER_PRIM_CAP) return;
  RenderPrim &prim = model.prims[model.count++];
  prim = RenderPrim{};
  prim.kind = PrimKind::FillTriangle;
  prim.x0 = x0;
  prim.y0 = y0;
  prim.x1 = x1;
  prim.y1 = y1;
  prim.w = x2;
  prim.h = y2;
}

void addText(RenderModel &model, const char *text, int16_t x, int16_t y,
             int16_t boxHeight, TextAlign align) {
  if (model.count >= RENDER_PRIM_CAP) return;
  RenderPrim &prim = model.prims[model.count++];
  prim = RenderPrim{};
  prim.kind = PrimKind::Text;
  prim.x0 = x;
  prim.y0 = y;
  prim.h = boxHeight;
  prim.align = static_cast<uint8_t>(align);
  clipText(prim.text, text, RENDER_TEXT_CAP - 1);
}

size_t clipText(char (&dst)[RENDER_TEXT_CAP], const char *src,
                size_t maxChars) {
  if (src == nullptr) {
    dst[0] = '\0';
    return 0;
  }
  size_t length = std::strlen(src);
  if (length > maxChars) length = maxChars;
  if (length > RENDER_TEXT_CAP - 1) length = RENDER_TEXT_CAP - 1;
  std::memcpy(dst, src, length);
  dst[length] = '\0';
  return length;
}

void formatRowValue(char (&dst)[RENDER_TEXT_CAP], const RenderRow &row) {
  char value[16];
  if (row.state[0] != '\0') {
    // Prefer the live HA state (on/off, sensor reading) over the numeric
    // glyph; categories and other items with no state stay empty instead of
    // showing a meaningless "0". The value column only fits 16 chars, and a
    // long state is clipped like any other span.
    std::snprintf(value, sizeof(value), "%.*s",
                  static_cast<int>(sizeof(value) - 1), row.state);
  } else if (row.value != 0.0f || row.editing) {
    const int truncated = static_cast<int>(row.value);
    const bool integral =
        row.value == static_cast<float>(truncated) && row.value >= -100000.0f &&
        row.value <= 100000.0f;
    if (integral) {
      std::snprintf(value, sizeof(value), "%.0f", row.value);
    } else {
      std::snprintf(value, sizeof(value), "%.1f", row.value);
    }
  } else {
    dst[0] = '\0';
    return;
  }
  char whole[40];
  std::snprintf(whole, sizeof(whole), "%s%s", value, row.unit);
  char tagged[48];  // "<" + whole (<= 39) + ">" + NUL provably fits
  if (row.editing) {
    std::snprintf(tagged, sizeof(tagged), "<%s>", whole);
  } else {
    std::snprintf(tagged, sizeof(tagged), "%s", whole);
  }
  const size_t valueChars =
      static_cast<size_t>((ROW_VALUE_RIGHT_X - ROW_NAME_CLIP_X) / MONO_CHAR_W);
  clipText(dst, tagged, valueChars);
}

void layoutNormalUi(const RenderSnapshot &snap, RenderModel &model) {
  // Clipped title text never reaches the power slot. No horizontal separator:
  // on the compact panel it visually cuts through adjacent text.
  char title[RENDER_TEXT_CAP];
  clipText(title, snap.title,
           static_cast<size_t>((TITLE_CLIP_X - TITLE_TEXT_X) / MONO_CHAR_W));
  addText(model, title, TITLE_TEXT_X, TITLE_STRIP_Y, TITLE_STRIP_HEIGHT,
          TextAlign::Left);

  // Four fixed item rows starting at y=24, each 26 px high. Selection is a
  // black cursor triangle in the left gutter; the
  // right-aligned value+unit span renders edit mode with angle brackets
  // without changing the row layout.
  const size_t shown = static_cast<size_t>(snap.rowCount) < VISIBLE_ROWS
                           ? static_cast<size_t>(snap.rowCount)
                           : VISIBLE_ROWS;
  for (size_t i = 0; i < shown; ++i) {
    const int16_t rowTop =
        ROW_AREA_Y + static_cast<int16_t>(i) * ROW_HEIGHT;
    const RenderRow &row = snap.rows[i];
    if (row.selected) {
      const int16_t cursorCy = rowTop + ROW_HEIGHT / 2;
      addFillTriangle(model, 2, cursorCy - 4, 8, cursorCy, 2, cursorCy + 4);
    }
    char name[RENDER_TEXT_CAP];
    clipText(name, row.name,
             static_cast<size_t>((ROW_NAME_CLIP_X - ROW_TEXT_X) / MONO_CHAR_W));
    addText(model, name, ROW_TEXT_X, rowTop, ROW_HEIGHT, TextAlign::Left);
    char value[RENDER_TEXT_CAP];
    formatRowValue(value, row);
    addText(model, value, ROW_VALUE_RIGHT_X, rowTop, ROW_HEIGHT,
            TextAlign::Right);
  }

  // Conditional scrollbar: track plus proportional thumb at x=291, only when
  // the page holds more than the four visible rows, bounded to y 24..127.
  if (scrollbarNeeded(snap.totalItems)) {
    addRect(model, SCROLLBAR_X, SCROLLBAR_AREA_TOP, SCROLLBAR_W,
            SCROLLBAR_AREA_HEIGHT, PRIM_COLOR_BLACK);
    int16_t thumbY = SCROLLBAR_AREA_TOP;
    int16_t thumbH = SCROLLBAR_AREA_HEIGHT;
    scrollbarThumb(snap.totalItems, snap.firstVisible, thumbY, thumbH);
    addFillRect(model, SCROLLBAR_X, thumbY, SCROLLBAR_W, thumbH,
                PRIM_COLOR_BLACK);
  }
}

void layoutPortalUi(const RenderSnapshot &snap, RenderModel &model) {
  addText(model, "MicroPad Setup", TITLE_TEXT_X, TITLE_STRIP_Y,
          TITLE_STRIP_HEIGHT, TextAlign::Left);

  const size_t rowChars =
      static_cast<size_t>((ROW_VALUE_RIGHT_X - ROW_TEXT_X) / MONO_CHAR_W);
  const int16_t rowTops[4] = {ROW_AREA_Y, ROW_AREA_Y + ROW_HEIGHT,
                              ROW_AREA_Y + 2 * ROW_HEIGHT,
                              ROW_AREA_Y + 3 * ROW_HEIGHT};
  char label[40];
  char span[RENDER_TEXT_CAP];

  std::snprintf(label, sizeof(label), "WiFi: %s", snap.portalSsid);
  clipText(span, label, rowChars);
  addText(model, span, ROW_TEXT_X, rowTops[0], ROW_HEIGHT, TextAlign::Left);

  std::snprintf(label, sizeof(label), "Pass: %s", snap.portalPassword);
  clipText(span, label, rowChars);
  addText(model, span, ROW_TEXT_X, rowTops[1], ROW_HEIGHT, TextAlign::Left);

  std::snprintf(label, sizeof(label), "Addr: %s", snap.portalAddress);
  clipText(span, label, rowChars);
  addText(model, span, ROW_TEXT_X, rowTops[2], ROW_HEIGHT, TextAlign::Left);

  std::snprintf(label, sizeof(label), "Open %s", snap.portalAddress);
  clipText(span, label, rowChars);
  addText(model, span, ROW_TEXT_X, rowTops[3], ROW_HEIGHT, TextAlign::Left);
}

void networkStatusPrims(uint8_t networkState, RenderModel &model) {
  const int16_t centerX = STATUS_CELL_X + STATUS_CELL_W / 2;
  const int16_t centerY = STATUS_CELL_Y + STATUS_CELL_H / 2;
  switch (networkState) {
    case NETWORK_STATE_MQTT: {
      // Solid 8x8 square centered in the cell.
      const int16_t side = 8;
      addFillRect(model, centerX - side / 2, centerY - side / 2, side, side,
                  PRIM_COLOR_BLACK);
      break;
    }
    case NETWORK_STATE_WIFI_ONLY: {
      // Two concentric outlined circles with the center erased: a ring.
      addCircle(model, centerX, centerY, 5, PRIM_COLOR_BLACK);
      addCircle(model, centerX, centerY, 3, PRIM_COLOR_BLACK);
      addFillCircle(model, centerX, centerY, 2, PRIM_COLOR_WHITE);
      break;
    }
    default: {
      // Connecting / disconnected: two 3x3 filled dots.
      addFillRect(model, centerX - 5, centerY - 1, 3, 3, PRIM_COLOR_BLACK);
      addFillRect(model, centerX + 3, centerY - 1, 3, 3, PRIM_COLOR_BLACK);
      break;
    }
  }
}

void powerIconPrims(bool usbHost, RenderModel &model) {
  if (!usbHost) return;
  const int16_t centerX = POWER_SLOT_X + POWER_SLOT_W / 2;
  const int16_t centerY = STATUS_CELL_Y + STATUS_CELL_H / 2;
  // USB trident drawn entirely from line/rectangle/circle prims (never text):
  // vertical stem, fork branches, arrow head, square and circular terminals.
  addLine(model, centerX, centerY - 2, centerX, centerY + 5);  // stem
  addLine(model, centerX, centerY - 2, centerX - 4, centerY);  // left branch
  addLine(model, centerX, centerY - 2, centerX + 4, centerY);  // right branch
  addLine(model, centerX - 4, centerY, centerX - 4, centerY + 2);
  addLine(model, centerX + 4, centerY, centerX + 4, centerY + 2);
  addLine(model, centerX - 4, centerY + 2, centerX, centerY + 6);
  addLine(model, centerX + 4, centerY + 2, centerX, centerY + 6);
  addRect(model, centerX - 7, centerY + 6, 4, 4, PRIM_COLOR_BLACK);
  addFillCircle(model, centerX + 7, centerY + 8, 2, PRIM_COLOR_BLACK);
}

void scrollbarThumb(uint8_t totalItems, uint8_t firstVisible,
                    int16_t &thumbY, int16_t &thumbH) {
  if (!scrollbarNeeded(totalItems)) {
    thumbY = SCROLLBAR_AREA_TOP;
    thumbH = SCROLLBAR_AREA_HEIGHT;
    return;
  }
  const int16_t trackH = SCROLLBAR_AREA_HEIGHT;
  thumbH = trackH * static_cast<int16_t>(VISIBLE_ROWS) /
           static_cast<int16_t>(totalItems);
  if (thumbH < 8) thumbH = 8;
  if (thumbH > trackH) thumbH = trackH;
  const int16_t range =
      static_cast<int16_t>(totalItems) - static_cast<int16_t>(VISIBLE_ROWS);
  int16_t pos = static_cast<int16_t>(firstVisible);
  if (pos > range) pos = range;
  if (pos < 0) pos = 0;
  thumbY = SCROLLBAR_AREA_TOP + (trackH - thumbH) * pos / range;
}

}  // namespace micropad
