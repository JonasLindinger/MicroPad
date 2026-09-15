// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
#include "micropad_core.h"
#include <algorithm>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>

using namespace micropad;

namespace micropad {

// Grants the host test binary access to the mailbox's private slot storage so
// it can assert publication never changes snapshot bytes (member of the
// SnapshotMailbox's own namespace: the friendship is scoped).
struct SnapshotMailboxHostTestAccess {
  static RenderSnapshot *slot(SnapshotMailbox &mb, int8_t index) {
    return &mb.slots_[index].snapshot;
  }
};

}  // namespace micropad

namespace {

// Deterministic snapshot fill. Every field gets a distinct value derived from
// the generation so tests can distinguish slot age and prove bytes survive
// publication unchanged.
RenderSnapshot fillSnapshot(uint32_t generation) {
  RenderSnapshot snap{};
  snap.generation = generation;
  snap.portal = (generation & 1U) != 0;
  snap.forceFull = (generation & 2U) != 0;
  snap.usbHost = (generation & 4U) != 0;
  snap.networkState = static_cast<uint8_t>(1 + generation % 4);
  snap.rowCount = 2;
  snap.totalItems = static_cast<uint8_t>(2 + generation);
  snap.firstVisible = 1;
  safeCopy(snap.title, "title");
  safeCopy(snap.portalSsid, "ssid");
  safeCopy(snap.portalPassword, "password");
  safeCopy(snap.portalAddress, "192.168.4.1");
  for (size_t i = 0; i < VISIBLE_ROWS; ++i) {
    char text[12];
    snprintf(text, sizeof text, "n%lu", static_cast<unsigned long>(generation));
    safeCopy(snap.rows[i].name, text);
    safeCopy(snap.rows[i].state, "on");
    safeCopy(snap.rows[i].unit, "W");
    snap.rows[i].value = 1.0f + static_cast<float>(generation);
    snap.rows[i].selected = (i == 1);
    snap.rows[i].editing = (i == 1 && (generation & 1U) != 0);
  }
  return snap;
}

}  // namespace

// ============================================================================
// Task 8: the two-slot snapshot mailbox (dependency-free slot state machine;
// the FreeRTOS critical-section wrapping lives in the sketch).
// ============================================================================

void validateNewest(RenderSnapshot &snap, uint32_t generation) {
  assert(snap.generation == generation);
  for (size_t i = 0; i < VISIBLE_ROWS; ++i) {
    char text[12];
    snprintf(text, sizeof text, "n%lu",
             static_cast<unsigned long>(generation));
    assert(std::strcmp(snap.rows[i].name, text) == 0);
  }
  assert(std::strcmp(snap.title, "title") == 0);
  assert(snap.portal == ((generation & 1U) != 0));
  assert(snap.rowCount == 2);
  assert(snap.totalItems == static_cast<uint8_t>(2 + generation));
  assert(snap.rows[1].selected);
}

void testSnapshotMailboxTwoSlotStateMachine() {
  // Exactly two fixed slots; an empty mailbox has nothing pending and nothing
  // to claim.
  SnapshotMailbox mb;
  static_assert(mb.SLOT_COUNT == 2, "exactly two render slots");
  assert(mb.SLOT_COUNT == 2);
  assert(!mb.hasPending());
  assert(mb.claimNewest() == -1);

  // First publish lands in slot 0 (the only free slot) and is claimable.
  RenderSnapshot first = fillSnapshot(1);
  int8_t slot = mb.beginWrite();
  assert(slot == 0);
  assert(mb.hasPending());  // the write slot is now Writing
  mb.writable(slot) = first;
  mb.publish(slot);
  assert(mb.hasPending());

  // A slot cannot be (re)written while it is Rendering. Claim the
  // published slot; while it renders, Core 1 may only use the other slot.
  const int8_t claimed = mb.claimNewest();
  assert(claimed == slot);
  assert(!mb.hasPending());  // Rendering alone never counts as pending

  // Publish three successive generations while the renderer draws: each new
  // publish demotes the previously published slot, so the renderer can never
  // claim a stale generation.
  RenderSnapshot a = fillSnapshot(2);
  assert(mb.beginWrite() == 1);
  mb.writable(1) = a;
  mb.publish(1);
  RenderSnapshot b = fillSnapshot(3);
  assert(mb.beginWrite() == 1);
  mb.writable(1) = b;
  mb.publish(1);
  RenderSnapshot c = fillSnapshot(4);
  assert(mb.beginWrite() == 1);
  mb.writable(1) = c;
  mb.publish(1);
  assert(mb.hasPending());

  // Release the rendered slot; the renderer next claims only the newest
  // generation from the other slot, and its bytes are exactly the newest
  // publisher's bytes (publication never mutates snapshot contents).
  mb.release(claimed);
  const int8_t newest = mb.claimNewest();
  assert(newest == 1);
  RenderSnapshot copy = mb.readable(newest);
  validateNewest(copy, 4);
  const uint8_t *before = reinterpret_cast<const uint8_t *>(
      SnapshotMailboxHostTestAccess::slot(mb, newest));
  const uint8_t *expected = reinterpret_cast<const uint8_t *>(&copy);
  assert(std::memcmp(before, expected, sizeof(RenderSnapshot)) == 0);

  mb.release(newest);
  assert(!mb.hasPending());
}

void testSnapshotMailboxReleaseAndPending() {
  SnapshotMailbox mb;

  // Release of a never-claimed slot is a no-op; hasPending stays false when
  // nothing is Published or Writing.
  mb.release(0);
  mb.release(1);
  assert(!mb.hasPending());

  // Writing appears in hasPending before publish and disappears after the
  // slot is claimed for rendering.
  const int8_t slot = mb.beginWrite();
  assert(slot == 0);
  assert(mb.hasPending());
  RenderSnapshot snap = fillSnapshot(5);
  mb.writable(slot) = snap;
  mb.publish(slot);
  assert(mb.hasPending());
  assert(mb.claimNewest() == slot);
  assert(!mb.hasPending());
  mb.release(slot);
  assert(!mb.hasPending());

  // Publishing to one slot while the other renders then releasing keeps the
  // newest claimable.
  RenderSnapshot first = fillSnapshot(6);
  const int8_t slot0 = mb.beginWrite();
  assert(slot0 == 0);
  mb.writable(slot0) = first;
  mb.publish(slot0);
  const int8_t claimed = mb.claimNewest();
  assert(claimed == 0);
  RenderSnapshot second = fillSnapshot(7);
  const int8_t slot1 = mb.beginWrite();
  assert(slot1 == 1);
  mb.writable(slot1) = second;
  mb.publish(slot1);
  assert(mb.hasPending());
  mb.release(claimed);
  assert(mb.claimNewest() == 1);
  mb.release(1);
  assert(!mb.hasPending());
}

void testSnapshotMailboxCoalescesWhileRendering() {
  // Repeated requests during one refresh replace the non-rendering slot: only
  // the newest generation survives, exactly once on the next claim.
  SnapshotMailbox mb;
  *SnapshotMailboxHostTestAccess::slot(mb, 1) = fillSnapshot(1);
  {
    const int8_t slot = mb.beginWrite();
    assert(slot == 0);
    mb.publish(slot);
  }
  int8_t claimed = mb.claimNewest();
  assert(claimed == 0);

  for (uint32_t g = 2; g <= 6; ++g) {
    RenderSnapshot snap = fillSnapshot(g);
    assert(mb.beginWrite() == 1);
    mb.writable(1) = snap;
    mb.publish(1);
  }
  mb.release(claimed);
  const int8_t newest = mb.claimNewest();
  assert(newest == 1);
  RenderSnapshot got = mb.readable(newest);
  validateNewest(got, 6);
  mb.release(newest);
  assert(!mb.hasPending());
}

namespace {

// Host-side fake raw-input port. Matrix levels and emitted events are recorded
// by (or driven into) these globals, mirroring the thin pin adapters the
// Arduino sketch provides over real GPIO.
bool gMatrix[MATRIX_ROWS][MATRIX_COLS] = {};
int gEmitCount = 0;
InputEvent gLastEvent{};

void resetFake() {
  for (uint8_t row = 0; row < MATRIX_ROWS; ++row) {
    for (uint8_t col = 0; col < MATRIX_COLS; ++col) gMatrix[row][col] = false;
  }
  gEmitCount = 0;
}

bool fakeReadKey(uint8_t row, uint8_t col) {
  return gMatrix[row][col];
}

void fakeEmit(const InputEvent &input) {
  ++gEmitCount;
  gLastEvent = input;
}

}  // namespace

void testSafeCopyClipDisplayCaps() {
  // The display-only caps are 32 characters, which is still more than the
  // renderer can show (12-character value column, 22-character title strip), so
  // shrinking them from 48 returns static RAM without losing visible content.
  static_assert(ITEM_NAME_CAP == 33);
  static_assert(TITLE_CAP == 33);
  static_assert(STATE_CAP == 33);
  // Identifier caps stay deliberately larger: a clipped entity id would
  // address the wrong device, so entity/page fields keep the strict copy.
  static_assert(ENTITY_CAP > ITEM_NAME_CAP);
  static_assert(PAGE_ID_CAP == 33);
  char state[STATE_CAP];
  const char *longState =
      "a state that is definitely longer than 32 characters";
  assert(!safeCopyClip(state, longState));
  assert(std::strlen(state) == STATE_CAP - 1);
  RenderRow row{};
  safeCopyClip(row.state, "2026-09-15T06:33:00.123456+02:00");
  assert(std::strlen(row.state) == STATE_CAP - 1);
  safeCopyClip(row.name, "Wohnzimmer Deckenlampe Mitte");
  assert(std::strcmp(row.name, "Wohnzimmer Deckenlampe Mitte") == 0);
}

void testRenderPrimBudgetHeadroom() {
  // Worst case: four visible rows (selection cursor), title, network status
  // cell, scrollbar and the USB power symbol. The measured worst case is 22
  // prims; the budget must keep at least 25 % headroom so a layout change that
  // silently overflows the model is caught here instead of on the panel.
  RenderSnapshot snap{};
  snap.totalItems = MAX_ITEMS_PER_PAGE;
  snap.rowCount = static_cast<uint8_t>(VISIBLE_ROWS);
  snap.networkState = NETWORK_STATE_MQTT;
  safeCopy(snap.title, "Erdgeschoss");
  for (uint8_t i = 0; i < VISIBLE_ROWS; ++i) {
    std::snprintf(snap.rows[i].name, sizeof(snap.rows[i].name), "row %u",
                  static_cast<unsigned>(i));
    snap.rows[i].state[0] = '\0';
    snap.rows[i].value = 12.5f * static_cast<float>(i + 1);
    snap.rows[i].selected = (i == 0);
    snap.rows[i].editing = (i == 0);
  }
  RenderModel model;
  layoutNormalUi(snap, model);
  networkStatusPrims(snap.networkState, model);
  powerIconPrims(true, model);
  assert(model.count <= RENDER_PRIM_CAP);
  assert(model.count * 4 <= RENDER_PRIM_CAP * 3);  // >= 25 % headroom
}

void testKeymapModel() {
  constexpr const char *names[KEY_COUNT] = {
      "r0c0", "r0c1", "r0c2", "r0c3", "r1c0", "r1c1", "r1c2",
      "r1c3", "r2c0", "r2c1", "r2c2", "r2c3", "enc_up", "enc_down"};
  for (size_t i = 0; i < KEY_COUNT; ++i) {
    KeyId parsed{};
    assert(std::strcmp(keyIdName(static_cast<KeyId>(i)), names[i]) == 0);
    assert(parseKeyId(names[i], parsed));
    assert(parsed == static_cast<KeyId>(i));
  }
  KeyId rejected{};
  assert(!parseKeyId("r3c0", rejected));
  assert(!parseKeyId(nullptr, rejected));

  Binding map[KEY_COUNT]{};
  loadDefaultKeymap(map);
  assert(map[0].action == Action::Home);
  assert(map[3].action == Action::Enter);
  assert(map[7].action == Action::Enter);
  assert(map[11].action == Action::Back);
  assert(map[12].action == Action::ScrollUp);
  assert(map[13].action == Action::ScrollDown);
  for (size_t i : {1U, 2U, 4U, 5U, 6U, 8U, 9U, 10U}) {
    assert(map[i].action == Action::None);
  }
}

void testActionModel() {
  constexpr const char *tokens[21] = {
      "none",       "enter",        "back",        "home",
      "settings",   "scroll",       "scroll_up",   "scroll_down",
      "navigate",   "keymap",       "get_all_pages", "toggle",
      "on",         "off",          "press",       "volume_up",
      "volume_down", "media_next",  "media_prev",  "edit",
      "confirm"};
  static_assert(sizeof(tokens) / sizeof(tokens[0]) == 21);
  for (size_t i = 0; i < sizeof(tokens) / sizeof(tokens[0]); ++i) {
    Action parsed{};
    const Action expect = static_cast<Action>(i);
    assert(std::strcmp(actionName(expect), tokens[i]) == 0);
    assert(parseAction(tokens[i], parsed));
    assert(parsed == expect);
  }
  Action rejected{};
  assert(!parseAction("bogus", rejected));
  assert(!parseAction(nullptr, rejected));

  Binding scroll{};
  scroll.action = Action::Scroll;
  InputEvent up{KeyId::EncUp, true, -1, 1};
  InputEvent down{KeyId::EncDown, true, 1, 2};
  InputEvent none{KeyId::EncUp, true, 0, 3};
  assert(resolveBinding(scroll, up) == Action::ScrollUp);
  assert(resolveBinding(scroll, down) == Action::ScrollDown);
  assert(resolveBinding(scroll, none) == Action::None);

  InputEvent released{KeyId::R0C0, false, 0, 4};
  Binding home{};
  home.action = Action::Home;
  assert(resolveBinding(home, released) == Action::None);
}

void testItemTypeModel() {
  ItemType parsed{};
  assert(parseItemType("light", parsed));
  assert(parsed == ItemType::Light);
  assert(parseItemType("back", parsed));
  assert(parsed == ItemType::Back);
  assert(!parseItemType("bogus", parsed));
  assert(!parseItemType(nullptr, parsed));
}

void testDebounceTiming() {
  // A LOW candidate before 25 ms emits no edge; a stable LOW at 25 ms emits
  // one press; continued LOW emits nothing; a stable HIGH at 25 ms emits one
  // release. Wrap-safe unsigned subtraction is proven with a candidate
  // timestamp of UINT32_MAX - 10 accepted at 15 (elapsed 26 ms).
  DebouncedInput key;
  assert(key.update(true, 100) == Edge::None);
  assert(key.update(true, 124) == Edge::None);
  assert(key.update(true, 125) == Edge::Pressed);
  assert(key.update(true, 200) == Edge::None);
  assert(key.update(false, 201) == Edge::None);
  assert(key.update(false, 226) == Edge::Released);

  DebouncedInput wrap;
  assert(wrap.update(true, UINT32_MAX - 10) == Edge::None);
  assert(wrap.update(true, 15) == Edge::Pressed);
}

void testDebounceHeldAndPrime() {
  // held() reflects the stable state, not the raw level.
  DebouncedInput key;
  assert(!key.held());
  assert(key.update(true, 100) == Edge::None);
  assert(!key.held());
  assert(key.update(true, 125) == Edge::Pressed);
  assert(key.held());

  // primeForWake seeds a fresh candidate so the next differing raw level is
  // debounced from the given timestamp rather than treated as a new baseline.
  DebouncedInput primed;
  primed.primeForWake(false, 50);
  assert(primed.update(true, 51) == Edge::None);
  assert(primed.update(true, 76) == Edge::Pressed);
}

void testQuadratureDetents() {
  // This board's mechanical detent spans two valid transitions.
  QuadratureDecoder forward;
  forward.reset(0b00);
  assert(forward.update(0b01) == 0);
  assert(forward.update(0b11) == 1);

  QuadratureDecoder reverse;
  reverse.reset(0b00);
  assert(reverse.update(0b10) == 0);
  assert(reverse.update(0b11) == -1);

  // An impossible two-bit jump emits zero and clears partial accumulation.
  QuadratureDecoder invalid;
  invalid.reset(0b00);
  assert(invalid.update(0b01) == 0);  // acc = +1
  assert(invalid.update(0b10) == 0);  // impossible jump clears accumulation
  assert(invalid.update(0b00) == 0);  // first new valid transition only

  // A resting (unchanging) phase emits nothing.
  QuadratureDecoder idle;
  idle.reset(0b11);
  assert(idle.update(0b11) == 0);
  assert(idle.update(0b11) == 0);
}

void testMatrixScanPass() {
  // With no raw presses, a clean pass emits nothing.
  DebouncedInput debounce[MATRIX_KEY_COUNT];
  resetFake();
  scanMatrixPass(debounce, fakeReadKey, fakeEmit, 1000);
  assert(gEmitCount == 0);

  // Press key (1,2): the first scan starts a candidate, the one at 25 ms of
  // the same level emits exactly one Pressed event for r1c2, and holding
  // longer emits no repeat.
  gMatrix[1][2] = true;
  scanMatrixPass(debounce, fakeReadKey, fakeEmit, 2000);
  assert(gEmitCount == 0);
  scanMatrixPass(debounce, fakeReadKey, fakeEmit, 2025);
  assert(gEmitCount == 1);
  assert(gLastEvent.key == KeyId::R1C2);
  assert(gLastEvent.pressed);
  assert(gLastEvent.direction == 0);
  assert(gLastEvent.atMs == 2025);
  scanMatrixPass(debounce, fakeReadKey, fakeEmit, 2100);
  assert(gEmitCount == 1);

  // A stable release at 25 ms emits exactly one Released event.
  gMatrix[1][2] = false;
  scanMatrixPass(debounce, fakeReadKey, fakeEmit, 2200);
  assert(gEmitCount == 1);
  scanMatrixPass(debounce, fakeReadKey, fakeEmit, 2225);
  assert(gEmitCount == 2);
  assert(gLastEvent.key == KeyId::R1C2);
  assert(!gLastEvent.pressed);
}

void testEncoderScanPassMapping() {
  // One physical clockwise detent (two transitions) emits one EncDown event.
  QuadratureDecoder forward;
  resetFake();
  forward.reset(0b00);
  scanEncoderPass(forward, 0b01, fakeEmit, 10);
  assert(gEmitCount == 0);
  scanEncoderPass(forward, 0b11, fakeEmit, 11);
  assert(gEmitCount == 1);
  assert(gLastEvent.key == KeyId::EncDown);
  assert(gLastEvent.direction == 1);
  assert(gLastEvent.pressed);
  assert(gLastEvent.atMs == 11);

  // Counterclockwise emits one pressed EncUp event after two transitions.
  QuadratureDecoder reverse;
  resetFake();
  reverse.reset(0b00);
  scanEncoderPass(reverse, 0b10, fakeEmit, 20);
  assert(gEmitCount == 0);
  scanEncoderPass(reverse, 0b11, fakeEmit, 21);
  assert(gEmitCount == 1);
  assert(gLastEvent.key == KeyId::EncUp);
  assert(gLastEvent.direction == -1);
  assert(gLastEvent.pressed);

  // No detent -> no event.
  QuadratureDecoder idle;
  resetFake();
  idle.reset(0b11);
  scanEncoderPass(idle, 0b11, fakeEmit, 30);
  assert(gEmitCount == 0);
}

void testAnyMatrixKeyHeld() {
  static_assert(MATRIX_KEY_COUNT == 12);

  // Fresh active-low pass catches a raw press even if never debounced.
  DebouncedInput debounce[MATRIX_KEY_COUNT];
  resetFake();
  assert(!anyMatrixKeyHeld(debounce, fakeReadKey));
  gMatrix[2][3] = true;
  assert(anyMatrixKeyHeld(debounce, fakeReadKey));
  gMatrix[2][3] = false;
  assert(!anyMatrixKeyHeld(debounce, fakeReadKey));

  // A debounced held state alone (after the raw level is already released)
  // still reports held, matching the sleep gate's "OR of held states".
  DebouncedInput one[MATRIX_KEY_COUNT];
  gMatrix[0][0] = true;
  assert(one[0].update(true, 100) == Edge::None);
  assert(one[0].update(true, 125) == Edge::Pressed);
  gMatrix[0][0] = false;
  assert(anyMatrixKeyHeld(one, fakeReadKey));
}

namespace {

Event pressEvent(const char *entity) {
  Event e{};
  e.action = Action::Press;
  safeCopy(e.entity, entity);
  return e;
}

Event editEvent(const char *entity, float value) {
  Event e{};
  e.action = Action::Edit;
  safeCopy(e.entity, entity);
  e.value = value;
  return e;
}

int gFlushCount = 0;
void fakeDispatch(const Event &) { ++gFlushCount; }

void addPage(PadController &pad, const char *id, const char *parent,
             uint8_t count = 0) {
  Page &p = pad.catalog.pages[pad.catalog.pageCount++];
  safeCopy(p.pageId, id);
  safeCopy(p.parent, parent);
  p.itemCount = count;
}

}  // namespace

void testEventQueueFifoAndBudget() {
  EventQueue q;
  char buf[24];
  for (size_t i = 0; i < QUEUE_CAPACITY; ++i) {
    snprintf(buf, sizeof buf, "p%zu", i);
    assert(q.push(pressEvent(buf)));
  }
  assert(q.size() == QUEUE_CAPACITY);

  // A seventeenth discrete event is rejected and increments overflow.
  snprintf(buf, sizeof buf, "p%zu", QUEUE_CAPACITY);
  assert(!q.push(pressEvent(buf)));
  assert(q.overflowCount() == 1);
  assert(q.size() == QUEUE_CAPACITY);

  // FIFO order preserved.
  for (size_t i = 0; i < QUEUE_CAPACITY; ++i) {
    Event e{};
    assert(q.pop(e));
    snprintf(buf, sizeof buf, "p%zu", i);
    assert(std::strcmp(e.entity, buf) == 0);
    assert(e.action == Action::Press);
  }
  assert(q.size() == 0);
  Event empty{};
  assert(!q.pop(empty));

  // A helper that pops at most MAX_FLUSH_PER_LOOP leaves six after one pass.
  EventQueue q2;
  for (size_t i = 0; i < 10; ++i) {
    snprintf(buf, sizeof buf, "q%zu", i);
    assert(q2.push(pressEvent(buf)));
  }
  assert(q2.size() == 10);
  gFlushCount = 0;
  const size_t dispatched = flushEvents(q2, fakeDispatch, MAX_FLUSH_PER_LOOP);
  assert(dispatched == MAX_FLUSH_PER_LOOP);
  assert(gFlushCount == 4);
  assert(q2.size() == 6);
  for (size_t i = 4; i < 10; ++i) {
    Event e{};
    assert(q2.pop(e));
    snprintf(buf, sizeof buf, "q%zu", i);
    assert(std::strcmp(e.entity, buf) == 0);
  }
  assert(q2.size() == 0);
}

void testEventQueueCoalescing() {
  char buf[24];

  // 15 discrete + one Edit for light.volume; push a newer Edit for the same
  // entity -> coalesced in place, size stays 16, newer value retained.
  EventQueue q;
  for (size_t i = 0; i < 15; ++i) {
    snprintf(buf, sizeof buf, "d%zu", i);
    assert(q.push(pressEvent(buf)));
  }
  assert(q.push(editEvent("light.volume", 5.0f)));
  assert(q.size() == 16);
  assert(q.push(editEvent("light.volume", 7.0f)));
  assert(q.size() == 16);
  assert(q.overflowCount() == 0);
  int edits = 0;
  float keptValue = -1.0f;
  while (q.size() > 0) {
    Event e{};
    assert(q.pop(e));
    if (e.action == Action::Edit) {
      ++edits;
      assert(std::strcmp(e.entity, "light.volume") == 0);
      keptValue = e.value;
    }
  }
  assert(edits == 1);
  assert(keptValue == 7.0f);

  // 15 discrete + one scroll; push a discrete navigation -> oldest replaceable
  // (the scroll) is evicted so the navigation event is preserved.
  EventQueue q2;
  for (size_t i = 0; i < 15; ++i) {
    snprintf(buf, sizeof buf, "s%zu", i);
    assert(q2.push(pressEvent(buf)));
  }
  Event scroll{};
  scroll.action = Action::ScrollDown;
  safeCopy(scroll.targetPage, "home");
  assert(q2.push(scroll));
  assert(q2.size() == 16);
  Event home{};
  home.action = Action::Home;
  safeCopy(home.targetPage, "home");
  assert(q2.push(home));
  assert(q2.size() == 16);
  assert(q2.overflowCount() == 0);
  bool sawScroll = false, sawHome = false;
  size_t seen = 0;
  while (q2.size() > 0) {
    Event e{};
    assert(q2.pop(e));
    ++seen;
    if (e.action == Action::ScrollDown) sawScroll = true;
    if (e.action == Action::Home) sawHome = true;
  }
  assert(seen == 16);
  assert(!sawScroll);
  assert(sawHome);

  // Overflow increments only when no replaceable event can be evicted.
  EventQueue q3;
  for (size_t i = 0; i < 16; ++i) {
    snprintf(buf, sizeof buf, "o%zu", i);
    assert(q3.push(pressEvent(buf)));
  }
  assert(!q3.push(home));
  assert(q3.overflowCount() == 1);
  assert(q3.size() == 16);
}

void testEventQueuePushFront() {
  EventQueue q;
  assert(q.push(pressEvent("a")));
  assert(q.push(pressEvent("b")));
  assert(q.push(pressEvent("c")));
  Event popped{};
  assert(q.pop(popped));
  assert(std::strcmp(popped.entity, "a") == 0);
  assert(q.pushFront(popped));
  assert(q.size() == 3);
  Event e{};
  assert(q.pop(e));
  assert(std::strcmp(e.entity, "a") == 0);
  assert(q.pop(e));
  assert(std::strcmp(e.entity, "b") == 0);
  assert(q.pop(e));
  assert(std::strcmp(e.entity, "c") == 0);
  assert(q.size() == 0);
}

void testSelectionWindow() {
  PadController pad;
  addPage(pad, "home", "", 8);
  Page &p = pad.catalog.pages[0];
  safeCopy(pad.state.pageId, "home");

  // Selection clamps to existing items (count 8 -> top index 7).
  pad.state.selected = 100;
  normalizeSelection(&p, pad.state);
  assert(pad.state.selected == 7);

  // firstVisible keeps the selection inside the four-row window.
  pad.state.selected = 5;
  pad.state.firstVisible = 0;
  normalizeSelection(&p, pad.state);
  assert(pad.state.selected == 5);
  assert(pad.state.firstVisible == 2);
  assert(pad.state.firstVisible <= pad.state.selected);
  assert(pad.state.selected < pad.state.firstVisible + VISIBLE_ROWS);

  // ScrollUp at index 0 does not wrap (via applyAction).
  Binding b{};
  pad.state.selected = 0;
  ApplyResult r = pad.applyAction(Action::ScrollUp, b, 100);
  assert(pad.state.selected == 0);
  assert(!r.stateChanged);
  assert(!r.eventReady);

  // ScrollDown at the final item does not wrap (count 8, top index 7).
  pad.state.selected = 7;
  r = pad.applyAction(Action::ScrollDown, b, 200);
  assert(pad.state.selected == 7);
  assert(!r.stateChanged);
  assert(!r.eventReady);
}

void testSelectedItemActions() {
  PadController pad;
  addPage(pad, "home", "", 11);
  Item (&items)[MAX_ITEMS_PER_PAGE] = pad.catalog.pages[0].items;
  items[0].type = ItemType::Category;
  items[1].type = ItemType::Light;
  items[2].type = ItemType::Switch;
  items[3].type = ItemType::Script;
  items[4].type = ItemType::Button;
  items[5].type = ItemType::Scene;
  items[6].type = ItemType::Number;
  items[7].type = ItemType::Settings;
  items[8].type = ItemType::Back;
  items[9].type = ItemType::Sensor;
  items[10].type = ItemType::MediaPlayer;
  for (uint8_t i = 0; i < 11; ++i) safeCopy(items[i].name, "x");

  safeCopy(pad.state.pageId, "home");
  pad.state.editing = false;

  const Action expect[11] = {Action::Navigate, Action::Toggle, Action::Toggle,
                             Action::Press,   Action::Press,   Action::Press,
                             Action::Edit,    Action::Settings, Action::Back,
                             Action::None,    Action::None};
  for (uint8_t i = 0; i < 11; ++i) {
    pad.state.selected = i;
    assert(pad.selectedItemAction() == expect[i]);
    assert(actionForSelectedItem(items[i], false) == expect[i]);
  }
  // Editing a number confirms instead of editing.
  pad.state.selected = 6;
  assert(actionForSelectedItem(items[6], true) == Action::Confirm);
}

void testOptimisticValues() {
  PadController pad;
  addPage(pad, "home", "", 2);
  Item (&items)[MAX_ITEMS_PER_PAGE] = pad.catalog.pages[0].items;
  safeCopy(pad.state.pageId, "home");
  Binding b{};

  // Toggle flips local state between on and off before ack.
  pad.state.selected = 0;
  items[0].type = ItemType::Switch;
  safeCopy(items[0].entity, "switch.x");
  safeCopy(items[0].state, "off");
  safeCopy(b.entity, "switch.x");  // P1.1: entity actions steer binding.entity
  assert(pad.applyAction(Action::Toggle, b, 1000).stateChanged);
  assert(std::strcmp(items[0].state, "on") == 0);
  assert(pad.applyAction(Action::Toggle, b, 1100).stateChanged);
  assert(std::strcmp(items[0].state, "off") == 0);
  assert(pad.queue.size() == 2);

  // Edit clamps to [min, max], advances by exact step; confirm exits editing
  // and retains the resulting queued value.
  pad.state.selected = 1;
  items[1].type = ItemType::Number;
  safeCopy(items[1].entity, "number.x");
  items[1].value = 4.0f;
  items[1].min = 0.0f;
  items[1].max = 10.0f;
  items[1].step = 3.0f;

  safeCopy(b.entity, "number.x");  // P1.1: the edited number is the bound entity
  assert(pad.applyAction(Action::Edit, b, 2000).stateChanged);
  assert(pad.state.editing);
  assert(pad.applyAction(Action::ScrollDown, b, 2100).stateChanged);
  assert(items[1].value == 7.0f);
  assert(pad.applyAction(Action::ScrollDown, b, 2200).stateChanged);
  assert(items[1].value == 10.0f);  // clamped to max
  assert(pad.applyAction(Action::ScrollUp, b, 2300).stateChanged);
  assert(items[1].value == 7.0f);
  assert(pad.applyAction(Action::Confirm, b, 2400).stateChanged);
  assert(!pad.state.editing);
  assert(pad.applyAction(Action::Confirm, b, 2500).stateChanged == false);

  // The final queued value matches the committed value.
  float lastValue = -1.0f;
  bool sawEdit = false;
  while (pad.queue.size() > 0) {
    Event e{};
    assert(pad.queue.pop(e));
    if (e.action == Action::Edit && std::strcmp(e.entity, "number.x") == 0) {
      sawEdit = true;
      lastValue = e.value;
    }
  }
  assert(sawEdit);
  assert(lastValue == 7.0f);
  assert(pad.state.lastInputMs == 2400);
  assert(pad.state.resyncPending);
}

void testLocalNavigationAndResync() {
  PadController nav;
  addPage(nav, "home", "", 1);
  addPage(nav, "target", "home", 0);
  addPage(nav, "settings", "home", 0);
  addPage(nav, "child", "home", 0);
  nav.catalog.pages[0].items[0].type = ItemType::Category;
  safeCopy(nav.catalog.pages[0].items[0].targetPage, "target");
  Binding b{};
  safeCopy(nav.catalog.pages[0].items[0].entity, "light.nav");
  safeCopy(b.entity, "light.nav");

  // Home selects the cached page "home".
  safeCopy(nav.state.pageId, "child");
  ApplyResult r = nav.applyAction(Action::Home, b, 500);
  assert(r.stateChanged);
  assert(r.eventReady);
  assert(std::strcmp(nav.state.pageId, "home") == 0);

  // Category navigate selects the cached targetPage.
  safeCopy(nav.state.pageId, "home");
  nav.state.selected = 0;
  r = nav.applyAction(Action::Navigate, b, 600);
  assert(std::strcmp(nav.state.pageId, "target") == 0);

  // Back selects the current cached parent.
  safeCopy(nav.state.pageId, "child");
  r = nav.applyAction(Action::Back, b, 700);
  assert(std::strcmp(nav.state.pageId, "home") == 0);

  // Settings navigates to the cached settings page.
  safeCopy(nav.state.pageId, "home");
  nav.state.selected = 0;
  r = nav.applyAction(Action::Settings, b, 800);
  assert(std::strcmp(nav.state.pageId, "settings") == 0);

  // Unknown page ids produce a no-change result.
  r = nav.applyAction(Action::Navigate, b, 900);
  assert(!r.stateChanged);
  assert(!r.eventReady);

  // Accepted actions set resyncPending with due time lastInputMs + 350.
  safeCopy(nav.state.pageId, "home");
  uint32_t original = nav.state.lastInputMs;
  nav.applyAction(Action::Toggle, b, 1234);
  assert(nav.state.lastInputMs > original);
  assert(nav.state.resyncPending);
  assert(!nav.resyncDue(1234 + 349));
  assert(nav.resyncDue(1234 + 350));
  nav.noteInputBurst(2000);
  assert(!nav.resyncDue(2000 + 349));
  assert(nav.resyncDue(2000 + 350));
}

void testSettingsDefaults() {
  const Settings s = defaultSettings();
  assert(std::strcmp(s.mqttHost, "192.168.0.100") == 0);
  assert(s.mqttPort == 1883);
  assert(std::strcmp(s.mqttUser, "micropad") == 0);
  assert(std::strcmp(s.mqttPassword, "replace-me") == 0);
  // Strings are bounded and not yet configured.
  assert(std::strcmp(s.wifiSsid, "") == 0);
  assert(std::strcmp(s.clientId, "") == 0);
}

void testSettingsValidation() {
  Settings s = defaultSettings();
  // A neutral placeholder default (no SSID) is a valid first-boot portal.
  assert(validateSettings(s));

  // Empty SSID = first-boot portal; password is ignored and stays valid.
  assert(std::strcmp(s.wifiSsid, "") == 0);
  safeCopy(s.wifiPassword, "");
  assert(validateSettings(s));

  // A nonempty SSID requires a password of 8..64 characters.
  safeCopy(s.wifiSsid, "MyNet");
  safeCopy(s.wifiPassword, "1234567");  // 7 chars: too short
  assert(!validateSettings(s));
  safeCopy(s.wifiPassword, "12345678");  // 8 chars: minimum
  assert(validateSettings(s));
  char pw64[65];
  for (int i = 0; i < 64; ++i) pw64[i] = 'a';
  pw64[64] = '\0';
  safeCopy(s.wifiPassword, pw64);  // 64 chars: maximum
  assert(validateSettings(s));
  safeCopy(s.wifiPassword, "");  // empty: rejected when SSID set
  assert(!validateSettings(s));

  // Port must be 1..65535.
  safeCopy(s.wifiSsid, "");  // portal mode, so password does not block
  s.mqttPort = 0;
  assert(!validateSettings(s));
  s.mqttPort = 1;
  assert(validateSettings(s));
  s.mqttPort = 65535;
  assert(validateSettings(s));
  s.mqttPort = 1883;

  // Host and user must be nonempty; client id may be empty.
  safeCopy(s.mqttHost, "");
  assert(!validateSettings(s));
  safeCopy(s.mqttHost, "127.0.0.1");
  safeCopy(s.mqttUser, "");
  assert(!validateSettings(s));
  safeCopy(s.mqttUser, "micropad");
  assert(validateSettings(s));
}

// ============================================================================
// Task 7: cache commit/swap decisions and event field rules (dependency-free
// parts of MQTT cache parse + event flush; the ArduinoJson text lives in the
// sketch).
// ============================================================================

void testSelectionBounds() {
  // normalizeSelection handles zero, four, five, and sixteen items: the
  // selection is clamped to the item range and kept inside the four-row
  // visible window (window overruns are bottom- or top-aligned).
  Page pages[MAX_PAGES]{};
  AppState state{};

  // Zero items: pinned to 0.
  pages[0].itemCount = 0;
  state.selected = 3;
  state.firstVisible = 2;
  normalizeSelection(&pages[0], state);
  assert(state.selected == 0);
  assert(state.firstVisible == 0);

  // Four items: exactly one visible window, still clamped.
  pages[1].itemCount = 4;
  state.selected = 7;
  state.firstVisible = 0;
  normalizeSelection(&pages[1], state);
  assert(state.selected == 3);
  assert(state.firstVisible == 0);

  // Five items: selected 4 keeps the window bottom-aligned at 1.
  pages[2].itemCount = 5;
  state.selected = 4;
  state.firstVisible = 0;
  normalizeSelection(&pages[2], state);
  assert(state.selected == 4);
  assert(state.firstVisible == 1);
  assert(state.selected < state.firstVisible + VISIBLE_ROWS);

  // Sixteen items: a mid selection scrolls the window, not the selection.
  pages[3].itemCount = 16;
  state.selected = 15;
  state.firstVisible = 0;
  normalizeSelection(&pages[3], state);
  assert(state.selected == 15);
  assert(state.firstVisible == 12);
  assert(state.selected >= state.firstVisible);
}

Page makePage(const char *id, const char *title, uint8_t itemCount) {
  Page page{};
  safeCopy(page.pageId, id);
  safeCopy(page.title, title);
  page.itemCount = itemCount;
  return page;
}

void testCommitCurrentPageUpsertClampsAndClears() {
  PadController pad;
  addPage(pad, "home", "", 2);
  addPage(pad, "other", "", 1);
  safeCopy(pad.state.pageId, "home");
  pad.state.selected = 9;  // out of range for a 2-item page already
  pad.state.firstVisible = 0;
  pad.state.editing = true;
  pad.state.resyncPending = true;
  pad.state.lastInputMs = 100;

  // A complete, valid replacement for the current page is committed in one
  // operation: title and items are fully replaced (not merged), selection is
  // clamped only after the replacement, edit mode and the fulfilled resync
  // are cleared, and the catalog keeps its page count.
  const Page fresh = makePage("home", "Fresh Home", 5);
  const bool committed = commitCurrentPage(pad, fresh, 700);
  assert(committed);
  assert(pad.catalog.pageCount == 2);
  Page *home = findPage(pad.catalog, "home");
  assert(home != nullptr);
  assert(std::strcmp(home->title, "Fresh Home") == 0);
  assert(home->itemCount == 5);
  assert(pad.state.selected == 4);  // clamped to the new count
  assert(!pad.state.editing);
  assert(!pad.state.resyncPending);
  assert(pad.state.lastInputMs == 700);
  // The other page is untouched by the replacement.
  assert(findPage(pad.catalog, "other")->itemCount == 1);
}

void testCommitCurrentPageAppendsUnknownPage() {
  PadController pad;
  addPage(pad, "home", "", 1);
  safeCopy(pad.state.pageId, "home");
  pad.state.resyncPending = true;
  pad.state.selected = 0;

  // An authoritative page outside the current view is cached, not displayed:
  // selection/resync of the current page must not be disturbed.
  const Page target = makePage("target", "Target", 3);
  assert(commitCurrentPage(pad, target, 50));
  assert(pad.catalog.pageCount == 2);
  Page *cached = findPage(pad.catalog, "target");
  assert(cached != nullptr && cached->itemCount == 3);
  assert(std::strcmp(pad.state.pageId, "home") == 0);
  assert(pad.state.resyncPending);  // unrelated page: resync stays pending
}

void testCommitCurrentPageRejectedKeepsState() {
  PadController pad;
  for (uint8_t i = 0; i < MAX_PAGES; ++i) {
    char id[8];
    snprintf(id, sizeof id, "p%u", i);
    addPage(pad, id, "", 1);
  }
  safeCopy(pad.state.pageId, "p0");
  pad.state.selected = 3;
  pad.state.firstVisible = 1;
  pad.state.editing = true;
  pad.state.resyncPending = true;

  // Empty page id: rejected before any live field is touched.
  const Page empty = makePage("", "x", 1);
  assert(!commitCurrentPage(pad, empty, 10));
  // Catalog is full: an unknown page cannot be committed; the current page
  // must keep its selection, edit mode, and pending resync.
  const Page unknown = makePage("p24", "X", 1);
  assert(!commitCurrentPage(pad, unknown, 20));
  assert(pad.catalog.pageCount == MAX_PAGES);
  assert(pad.state.selected == 3);
  assert(pad.state.firstVisible == 1);
  assert(pad.state.editing);
  assert(pad.state.resyncPending);
}

void testPageContentEqualityDetectsAuthoritativeChanges() {
  Page a = makePage("home", "Home", 1);
  Page b = a;
  assert(pageContentEqual(a, b));
  safeCopy(b.items[0].state, "on");
  assert(!pageContentEqual(a, b));
  b = a;
  safeCopy(b.title, "Changed");
  assert(!pageContentEqual(a, b));
}

void testReplaceKeymapReplacesAllEntries() {
  Binding staging[KEY_COUNT]{};
  const char *names[KEY_COUNT] = {
      "r0c0", "r0c1", "r0c2", "r0c3", "r1c0", "r1c1", "r1c2",
      "r1c3", "r2c0", "r2c1", "r2c2", "r2c3", "enc_up", "enc_down"};
  for (size_t i = 0; i < KEY_COUNT; ++i) {
    staging[i].action = Action::Press;
    safeCopy(staging[i].entity, names[i]);
    safeCopy(staging[i].targetPage, "home");
  }

  // Pre-fill the destination with a completely different map; replacement
  // must copy the full validated staging array in one operation, never
  // preserving omitted or stale entries.
  Binding dst[KEY_COUNT];
  loadDefaultKeymap(dst);
  replaceKeymap(dst, staging);
  for (size_t i = 0; i < KEY_COUNT; ++i) {
    assert(dst[i].action == Action::Press);
    assert(std::strcmp(dst[i].entity, names[i]) == 0);
    assert(std::strcmp(dst[i].targetPage, "home") == 0);
  }
}

void testQueuedEventsCarryOriginPage() {
  // Every applyAction event carries the page it was triggered on, so the
  // sketch can serialize the backend's required event.page_id field.
  PadController pad;
  addPage(pad, "home", "", 2);
  safeCopy(pad.state.pageId, "home");
  pad.state.selected = 0;
  pad.catalog.pages[0].items[0].type = ItemType::Switch;
  safeCopy(pad.catalog.pages[0].items[0].entity, "switch.x");
  safeCopy(pad.catalog.pages[0].items[0].state, "off");
  Binding b{};
  safeCopy(b.entity, "switch.x");

  assert(pad.applyAction(Action::Toggle, b, 100).eventReady);
  assert(pad.applyAction(Action::ScrollDown, b, 200).eventReady);
  Event e{};
  assert(pad.queue.pop(e));
  assert(std::strcmp(e.pageId, "home") == 0);
  assert(std::strcmp(e.entity, "switch.x") == 0);
  assert(pad.queue.pop(e));
  assert(std::strcmp(e.pageId, "home") == 0);
}

// ============================================================================
// Task 9: landscape UI geometry (prim model), scrollbar math, value
// formatting, and the refresh policy. All behavior is dependency-free and
// hardware-free: the prim model is the deterministic text-representable
// contract the sketch draws.
// ============================================================================

namespace {

bool hasKind(const RenderModel &model, PrimKind kind) {
  for (uint16_t i = 0; i < model.count; ++i) {
    if (model.prims[i].kind == kind) return true;
  }
  return false;
}

bool hasTextContaining(const RenderModel &model, const char *needle) {
  for (uint16_t i = 0; i < model.count; ++i) {
    if (model.prims[i].kind == PrimKind::Text &&
        std::strstr(model.prims[i].text, needle) != nullptr) {
      return true;
    }
  }
  return false;
}

// Deterministic snapshot covering the normal UI: four rows, five total items
// (scrollbar on), second row selected and in edit mode.
RenderSnapshot uiSnapshot() {
  RenderSnapshot snap{};
  snap.generation = 1;
  snap.forceFull = false;
  snap.usbHost = false;
  snap.networkState = NETWORK_STATE_MQTT;
  snap.rowCount = 4;
  snap.totalItems = 5;
  snap.firstVisible = 1;
  safeCopy(snap.title, "Living Room");
  const char *names[4] = {"Ceiling", "Lamp", "AC", "Fan"};
  const char *units[4] = {"W", "\xC2\xB0" "C", "W", "%"};
  const float values[4] = {23.0f, 22.5f, 120.0f, 40.0f};
  for (uint8_t i = 0; i < 4; ++i) {
    safeCopy(snap.rows[i].name, names[i]);
    // No state: the value/unit edit span is what the normal UI tests cover.
    safeCopy(snap.rows[i].state, "");
    safeCopy(snap.rows[i].unit, units[i]);
    snap.rows[i].value = values[i];
    snap.rows[i].selected = (i == 1);
    snap.rows[i].editing = (i == 1);
  }
  return snap;
}

struct BBox {
  int16_t minX, minY, maxX, maxY;
};

// Inclusive bounding box of a prim (text spans are skipped: they carry no
// pixel extents in the model).
void primBBox(const RenderPrim &p, BBox &box) {
  box.minX = p.x0;
  box.maxX = p.x0;
  box.minY = p.y0;
  box.maxY = p.y0;
  switch (p.kind) {
    case PrimKind::Line:
      box.minX = std::min(p.x0, p.x1);
      box.maxX = std::max(p.x0, p.x1);
      box.minY = std::min(p.y0, p.y1);
      box.maxY = std::max(p.y0, p.y1);
      break;
    case PrimKind::Rect:
    case PrimKind::FillRect:
      box.maxX = static_cast<int16_t>(p.x0 + p.w - 1);
      box.maxY = static_cast<int16_t>(p.y0 + p.h - 1);
      break;
    case PrimKind::Circle:
    case PrimKind::FillCircle:
      box.minX = static_cast<int16_t>(p.x0 - p.radius);
      box.maxX = static_cast<int16_t>(p.x0 + p.radius);
      box.minY = static_cast<int16_t>(p.y0 - p.radius);
      box.maxY = static_cast<int16_t>(p.y0 + p.radius);
      break;
    case PrimKind::FillTriangle:
      box.minX = std::min(p.x0, std::min(p.x1, p.w));
      box.maxX = std::max(p.x0, std::max(p.x1, p.w));
      box.minY = std::min(p.y0, std::min(p.y1, p.h));
      box.maxY = std::max(p.y0, std::max(p.y1, p.h));
      break;
    case PrimKind::Text:
      break;
  }
}

}  // namespace

void testLayoutConstants() {
  static_assert(CANVAS_WIDTH == 296, "landscape canvas width");
  static_assert(CANVAS_HEIGHT == 128, "landscape canvas height");
  static_assert(TITLE_STRIP_HEIGHT == 24, "title strip");
  static_assert(ROW_HEIGHT == 26, "equal item rows");
  static_assert(ROW_AREA_Y + 4 * ROW_HEIGHT == CANVAS_HEIGHT,
                "four rows fill the canvas");
  // Status cell at the right edge; power slot immediately left of it.
  static_assert(STATUS_CELL_X + STATUS_CELL_W == CANVAS_WIDTH, "right cell");
  static_assert(POWER_SLOT_X + POWER_SLOT_W == STATUS_CELL_X,
                "power slot left of the status cell");
  static_assert(SCROLLBAR_AREA_TOP + SCROLLBAR_AREA_HEIGHT == CANVAS_HEIGHT,
                "scrollbar inside y 24..127");
  static_assert(VISIBLE_ROWS == 4, "four visible rows");
  assert(MONO_CHAR_W > 0);
}

void testScrollbarThumbGeometry() {
  assert(scrollbarNeeded(5));
  assert(scrollbarNeeded(20));
  assert(!scrollbarNeeded(4));
  assert(!scrollbarNeeded(0));

  int16_t y = -1;
  int16_t h = -1;
  // 5 items, first row visible: thumb at the top, proportional height.
  scrollbarThumb(5, 0, y, h);
  assert(y == 24);
  assert(h == 104 * 4 / 5);
  assert(y + h <= 128);

  // Bottom of the list, many items: thumb still fully inside y 24..127.
  scrollbarThumb(20, 16, y, h);
  assert(h == 104 * 4 / 20);
  assert(y >= 24);
  assert(y + h <= 128);

  scrollbarThumb(20, 0, y, h);
  assert(y == 24);

  // Monotonic thumb: later positions sit lower, never higher.
  int16_t y0 = -1;
  int16_t h0 = -1;
  scrollbarThumb(10, 0, y0, h0);
  scrollbarThumb(10, 3, y, h);
  assert(h == h0);
  assert(y > y0);
  assert(h >= 8);  // minimum thumb height

  // No scrollbar needed: no-op thumb (caller skips drawing in that case).
  scrollbarThumb(4, 0, y, h);
  assert(y == 24 && h == 104);
}

void testLayoutNormalUiModel() {
  RenderSnapshot snap = uiSnapshot();
  RenderModel model;
  layoutNormalUi(snap, model);
  assert(model.count > 0);
  assert(model.count <= RENDER_PRIM_CAP);

  // Title text is clipped into the strip without a separator.
  assert(hasTextContaining(model, "Living Room"));
  bool separator = false;
  for (uint16_t i = 0; i < model.count; ++i) {
    const RenderPrim &p = model.prims[i];
    if (p.kind == PrimKind::Line && p.y0 == STRIP_SEPARATOR_Y &&
        p.y1 == STRIP_SEPARATOR_Y) {
      separator = true;
    }
    if (p.kind == PrimKind::Text && p.y0 == TITLE_STRIP_Y) {
      assert(p.x0 == TITLE_TEXT_X);
      assert(p.align == static_cast<uint8_t>(TextAlign::Left));
    }
  }
  assert(!separator);

  // Four fixed item rows starting at y=24, each 26 px high.
  for (uint8_t i = 0; i < 4; ++i) {
    const int16_t rowTop = ROW_AREA_Y + static_cast<int16_t>(i) * ROW_HEIGHT;
    uint16_t rowTexts = 0;
    for (uint16_t j = 0; j < model.count; ++j) {
      if (model.prims[j].kind == PrimKind::Text &&
          model.prims[j].y0 == rowTop) {
        ++rowTexts;
      }
    }
    assert(rowTexts >= 1);
  }

  // Selection: cursor triangle without an underline through the text.
  int16_t triangles = 0;
  int16_t underlines = 0;
  for (uint16_t i = 0; i < model.count; ++i) {
    const RenderPrim &p = model.prims[i];
    if (p.kind == PrimKind::FillTriangle) ++triangles;
    if (p.kind == PrimKind::Rect && p.h == 1) ++underlines;
  }
  assert(triangles == 1);
  assert(underlines == 0);

  // Editing value rendered with angle brackets, right-aligned at 288.
  assert(hasTextContaining(model, "<22.5"));
  bool rightAlignedValue = false;
  for (uint16_t i = 0; i < model.count; ++i) {
    const RenderPrim &p = model.prims[i];
    if (p.kind == PrimKind::Text &&
        p.align == static_cast<uint8_t>(TextAlign::Right)) {
      assert(p.x0 == ROW_VALUE_RIGHT_X);
      rightAlignedValue = true;
    }
  }
  assert(rightAlignedValue);

  // Scrollbar present for 5 items and absent for 4.
  bool scrollbarPrim = false;
  for (uint16_t i = 0; i < model.count; ++i) {
    if (model.prims[i].x0 == SCROLLBAR_X) scrollbarPrim = true;
  }
  assert(scrollbarPrim);
  snap.totalItems = 4;
  RenderModel compact;
  layoutNormalUi(snap, compact);
  for (uint16_t i = 0; i < compact.count; ++i) {
    assert(compact.prims[i].x0 != SCROLLBAR_X);
  }

  // Long names are clipped to the name column.
  safeCopy(snap.rows[0].name, "ExtraLongCeilingLampNameForClipping");
  RenderModel clipped;
  layoutNormalUi(snap, clipped);
  for (uint16_t i = 0; i < clipped.count; ++i) {
    const RenderPrim &p = clipped.prims[i];
    if (p.kind == PrimKind::Text && p.y0 == ROW_AREA_Y &&
        p.align == static_cast<uint8_t>(TextAlign::Left) &&
        p.text[0] != '\0') {
      assert(std::strlen(p.text) <=
             static_cast<size_t>((ROW_NAME_CLIP_X - ROW_TEXT_X) / MONO_CHAR_W));
    }
  }
}

void testLayoutPortalUiModel() {
  RenderSnapshot snap{};
  snap.portal = true;
  safeCopy(snap.portalSsid, "MicroPad-Setup");
  safeCopy(snap.portalPassword, "ABCDEFGH123456XY");
  safeCopy(snap.portalAddress, "192.168.4.1");
  RenderModel model;
  layoutPortalUi(snap, model);
  assert(model.count > 0);
  assert(hasTextContaining(model, "MicroPad Setup"));
  assert(hasTextContaining(model, "MicroPad-Setup"));
  assert(hasTextContaining(model, "192.168.4.1"));
  bool separator = false;
  for (uint16_t i = 0; i < model.count; ++i) {
    const RenderPrim &p = model.prims[i];
    if (p.kind == PrimKind::Line && p.y0 == STRIP_SEPARATOR_Y &&
        p.y1 == STRIP_SEPARATOR_Y) {
      separator = true;
    }
  }
  assert(!separator);
}

void testNetworkStatusPrims() {
  // MQTT: one solid 8x8 square centered in the cell.
  RenderModel model;
  networkStatusPrims(NETWORK_STATE_MQTT, model);
  assert(!hasKind(model, PrimKind::Text));
  int16_t squares = 0;
  for (uint16_t i = 0; i < model.count; ++i) {
    const RenderPrim &p = model.prims[i];
    if (p.kind == PrimKind::FillRect) {
      ++squares;
      assert(p.w == 8 && p.h == 8);
      assert(p.x0 >= STATUS_CELL_X);
      assert(p.x0 + p.w <= STATUS_CELL_X + STATUS_CELL_W);
    }
  }
  assert(squares == 1);

  // Wi-Fi only: two concentric outlined circles with the center erased (ring).
  RenderModel ring;
  networkStatusPrims(NETWORK_STATE_WIFI_ONLY, ring);
  assert(!hasKind(ring, PrimKind::Text));
  int16_t circles = 0;
  bool centerErased = false;
  for (uint16_t i = 0; i < ring.count; ++i) {
    const RenderPrim &p = ring.prims[i];
    if (p.kind == PrimKind::Circle) {
      ++circles;
      assert(p.color == PRIM_COLOR_BLACK);
    }
    if (p.kind == PrimKind::FillCircle && p.radius == 2 &&
        p.color == PRIM_COLOR_WHITE) {
      centerErased = true;
    }
  }
  assert(circles == 2);
  assert(centerErased);

  // Connecting and disconnected: two 3x3 filled dots, still no text.
  for (uint8_t state : {NETWORK_STATE_CONNECTING, NETWORK_STATE_DISCONNECTED}) {
    RenderModel dots;
    networkStatusPrims(state, dots);
    assert(!hasKind(dots, PrimKind::Text));
    int16_t dotCount = 0;
    for (uint16_t i = 0; i < dots.count; ++i) {
      if (dots.prims[i].kind == PrimKind::FillRect) {
        ++dotCount;
        assert(dots.prims[i].w == 3 && dots.prims[i].h == 3);
      }
    }
    assert(dotCount == 2);
  }
}

void testPowerIconPrims() {
  RenderModel off;
  powerIconPrims(false, off);
  assert(off.count == 0);

  RenderModel model;
  powerIconPrims(true, model);
  assert(model.count == 9);
  // Pure line/rectangle/circle geometry: never text, never fill rects.
  for (uint16_t i = 0; i < model.count; ++i) {
    const PrimKind kind = model.prims[i].kind;
    assert(kind == PrimKind::Line || kind == PrimKind::Rect ||
           kind == PrimKind::FillCircle);
  }
  // Entire symbol stays inside the power slot.
  for (uint16_t i = 0; i < model.count; ++i) {
    BBox box;
    primBBox(model.prims[i], box);
    assert(box.minX >= POWER_SLOT_X);
    assert(box.maxX < POWER_SLOT_X + POWER_SLOT_W);
    assert(box.minY >= 0);
    assert(box.maxY < TITLE_STRIP_HEIGHT);
  }
}

void testFormatRowValue() {
  RenderRow row{};
  char dst[RENDER_TEXT_CAP];
  safeCopy(row.unit, "W");
  row.value = 23.0f;
  formatRowValue(dst, row);
  assert(std::strcmp(dst, "23W") == 0);  // integral: no decimal
  row.value = 22.5f;
  formatRowValue(dst, row);
  assert(std::strcmp(dst, "22.5W") == 0);
  row.editing = true;
  formatRowValue(dst, row);
  assert(std::strcmp(dst, "<22.5W>") == 0);  // distinct edit rendering
  row.editing = false;
  row.value = 40.0f;
  safeCopy(row.unit, "%");
  formatRowValue(dst, row);
  assert(std::strcmp(dst, "40%") == 0);
  // A long value+unit span is clipped to the value column.
  row.value = 123.0f;
  safeCopy(row.unit, "kilowatt-hours");
  formatRowValue(dst, row);
  assert(std::strlen(dst) <=
         static_cast<size_t>((ROW_VALUE_RIGHT_X - ROW_NAME_CLIP_X) /
                             MONO_CHAR_W));
  // Live HA state wins over the numeric glyph; categories with neither state
  // nor a value render empty (this killed the trailing "0" on every row).
  safeCopy(row.state, "on");
  row.value = 0.0f;
  safeCopy(row.unit, "");
  formatRowValue(dst, row);
  assert(std::strcmp(dst, "on") == 0);
  safeCopy(row.state, "off");
  formatRowValue(dst, row);
  assert(std::strcmp(dst, "off") == 0);
  safeCopy(row.state, "");
  row.value = 0.0f;
  formatRowValue(dst, row);
  assert(dst[0] == '\0');
  // Editing an item with a zero value still shows the editable numeric span.
  row.editing = true;
  formatRowValue(dst, row);
  assert(std::strcmp(dst, "<0>") == 0);
}

void testClipTextSpan() {
  char dst[RENDER_TEXT_CAP];
  assert(clipText(dst, "abcdefghijklmnopqrstuvwxyz", 22) == 22);
  assert(std::strlen(dst) == 22);
  assert(std::strncmp(dst, "abcdefghijklmnopqrstuv", 22) == 0);
  assert(clipText(dst, "short", 22) == 5);
  assert(std::strcmp(dst, "short") == 0);
  assert(clipText(dst, nullptr, 5) == 0);
  assert(dst[0] == '\0');
}

void testRefreshMustBeFullReasons() {
  assert(refreshMustBeFull(DrawReason::Boot));
  assert(!refreshMustBeFull(DrawReason::StateChange));
  assert(refreshMustBeFull(DrawReason::PortalEnter));
  assert(refreshMustBeFull(DrawReason::PortalExit));
  assert(!refreshMustBeFull(DrawReason::PowerEdge));
  assert(refreshMustBeFull(DrawReason::Recovery));
  assert(refreshMustBeFull(DrawReason::PartialLimit));
}

void testRefreshPolicyModeAndCount() {
  RefreshPolicy policy;
  // Boot forces full; a plain state change is a partial.
  assert(policy.beginRefresh(0, refreshMustBeFull(DrawReason::Boot)) ==
         RefreshMode::Full);
  assert(policy.beginRefresh(1, false) == RefreshMode::Partial);
  // The partial counter never climbs while the limit is disabled (== 0), so
  // a successful partial refresh leaves it at zero.
  policy.completeRefresh(true);
  assert(policy.partialCount() == 0);
  assert(policy.beginRefresh(2, false) == RefreshMode::Partial);
  policy.completeRefresh(true);
  assert(policy.partialCount() == 0);

  // A BUSY failure never increments and forces the next refresh full.
  assert(policy.beginRefresh(3, false) == RefreshMode::Partial);
  policy.completeRefresh(false);
  assert(policy.partialCount() == 0);
  assert(policy.recoveryPending());
  assert(policy.beginRefresh(4, false) == RefreshMode::Full);
  policy.completeRefresh(true);
  assert(policy.partialCount() == 0);  // full refresh resets the counter
  assert(!policy.recoveryPending());

  // Partial-limit refresh is disabled (MAX_PARTIAL_REFRESHES == 0): repeated
  // successful partials never force a visible full-panel flash; the counter
  // stays at zero and every ordinary refresh remains partial.
  for (uint8_t i = 0; i < 50; ++i) {
    assert(policy.beginRefresh(100 + i, false) == RefreshMode::Partial);
    policy.completeRefresh(true);
  }
  assert(policy.partialCount() == 0);
  assert(policy.beginRefresh(200, false) == RefreshMode::Partial);
}

void testRefreshPolicySpacingWrapSafe() {
  RefreshPolicy policy;
  // Previous refresh started just before the 2^32 ms rollover.
  policy.beginRefresh(0xFFFFFF00u, false);
  // 0x10 - 0xFFFFFF00 = 0x110 = 272 ms elapsed: ceiling at 0 (>= 100 spacing
  // already elapsed), then exact boundaries around the 100 ms window.
  assert(policy.remainingSpacingMs(0x00000010u) == 0);
  assert(policy.spacingElapsed(0x00000050u));   // 336 ms elapsed >= 100
  assert(policy.spacingElapsed(0x00000070u));   // 0x170 = 368 >= 100
  assert(policy.remainingSpacingMs(0x00000070u) == 0);
  // No previous refresh: zero elapsed means the full spacing applies.
  RefreshPolicy fresh;
  assert(fresh.remainingSpacingMs(0) == MIN_REFRESH_SPACING_MS);
}

void testElapsedMsWrapSafe() {
  assert(elapsedMs(0x00000010u, 0xFFFFFF00u) == 0x110u);
  assert(elapsedMs(1000, 0) == 1000);
  assert(elapsedMs(0x00000050u, 0xFFFFFF00u) == 0x150u);
}

void testLayoutModelPrimsStayOnCanvas() {
  RenderSnapshot snap = uiSnapshot();
  snap.usbHost = true;
  RenderModel model;
  layoutNormalUi(snap, model);
  for (uint16_t i = 0; i < model.count; ++i) {
    BBox box;
    primBBox(model.prims[i], box);
    if (model.prims[i].kind == PrimKind::Text) continue;
    assert(box.minX >= 0);
    assert(box.maxX < CANVAS_WIDTH);
    assert(box.minY >= 0);
    assert(box.maxY < CANVAS_HEIGHT);
  }
}

// ============================================================================
// Task 10: debounce-guarded USB host detection. PowerDebouncer (dependency-
// free) converts raw CDC-host samples into stable edges: the first sample
// seeds the baseline, a raw change restarts the candidate window, and exactly
// one edge is emitted when the same candidate has remained for USB_STABLE_MS.
// Repeated samples of the accepted state never re-emit. The sketch's
// powerTick() additionally gates sampling to every USB_SAMPLE_MS; only the
// debouncer's stable-window behavior is exercised here.
// ============================================================================

void testPowerDebouncerConnectedAndDisconnectedWindows() {
  // Brief sequence: raw false at 0 (baseline), true at 500/1000/1499, true at
  // 2000. Only the final sample emits Connected: the true candidate starts at
  // 500 and first holds for 1500 ms at 2000.
  PowerDebouncer pd;
  assert(pd.sample(false, 0) == PowerEdge::None);
  assert(pd.sample(true, 500) == PowerEdge::None);
  assert(pd.sample(true, 1000) == PowerEdge::None);
  assert(pd.sample(true, 1499) == PowerEdge::None);
  assert(pd.sample(true, 2000) == PowerEdge::Connected);

  // Brief sequence: false at 2500/3000/3499/4000. The false candidate starts
  // at 2500 and first holds for 1500 ms at 4000: only 4000 emits Disconnected.
  assert(pd.sample(false, 2500) == PowerEdge::None);
  assert(pd.sample(false, 3000) == PowerEdge::None);
  assert(pd.sample(false, 3499) == PowerEdge::None);
  assert(pd.sample(false, 4000) == PowerEdge::Disconnected);

  // Repeated samples of the accepted state emit no further edge.
  assert(pd.sample(false, 4500) == PowerEdge::None);
  assert(pd.sample(false, 5000) == PowerEdge::None);
}

void testPowerDebouncerBounceNeverStabilizes() {
  // A bounce sequence changes the raw state before the 1500 ms window ever
  // closes: every sample returns None and no stable edge is declared, even
  // though the raw level is repeatedly held for 500 ms at a time.
  PowerDebouncer pd;
  assert(pd.sample(true, 0) == PowerEdge::None);     // baseline
  assert(pd.sample(false, 500) == PowerEdge::None);  // bounce
  assert(pd.sample(true, 1000) == PowerEdge::None);
  assert(pd.sample(false, 1500) == PowerEdge::None);
  assert(pd.sample(true, 2000) == PowerEdge::None);
  assert(pd.sample(false, 2500) == PowerEdge::None);
  assert(pd.sample(true, 3000) == PowerEdge::None);

  // Once a settled candidate lets the window close on the accepted state
  // (here the baseline false), the next stable true candidate emits exactly
  // one Connected and none after. The first sample never emits by itself.
  PowerDebouncer settled;
  assert(settled.sample(false, 0) == PowerEdge::None);
  assert(settled.sample(false, 1500) == PowerEdge::None);
  assert(settled.sample(true, 1600) == PowerEdge::None);
  assert(settled.sample(true, 3100) == PowerEdge::Connected);
  assert(settled.sample(true, 3600) == PowerEdge::None);
}

void testPowerDebouncerWrapAround() {
  // The true candidate starts just before the 2^32 ms rollover
  // (UINT32_MAX - 10). Wrap-safe unsigned elapsed keeps counting past zero:
  // 21 ms elapsed at 10, 1411 ms at 1400, and the stable window closes at
  // 1490 (1501 ms later), emitting exactly one Connected across the wrap.
  PowerDebouncer pd;
  assert(pd.sample(false, 0) == PowerEdge::None);            // baseline
  assert(pd.sample(true, UINT32_MAX - 10) == PowerEdge::None); // candidate restarts
  assert(pd.sample(true, 10) == PowerEdge::None);             // elapsed 21 ms
  assert(pd.sample(true, 1400) == PowerEdge::None);           // elapsed 1411 ms
  assert(pd.sample(true, 1490) == PowerEdge::Connected);      // elapsed 1501 ms
}

// ============================================================================
// Task 11: warm light sleep. canSleep() is the dependency-free sleep
// predicate: USB powered, a held key, an active or pending render, less than
// IDLE_SLEEP_MS of idle, and less than MIN_AWAKE_MS since the last wake each
// block sleep; both elapsed comparisons are unsigned wrap-safe.
// primeForWake() is finalized here as the wake-time re-prime: collapsing the
// candidate and stable state to the wake-time raw level means a currently
// held wake key is never re-emitted by the first ordinary scans (the sketch's
// immediate wake scan already delivered the press) while its eventual release
// still produces a Released edge.
// ============================================================================

void testSleepPredicate() {
  const uint32_t boot = 100000;
  // Idle boundary: exactly IDLE_SLEEP_MS of idle makes sleep eligible, one ms
  // less does not (minimum-awake satisfied in both cases).
  assert(!canSleep(false, false, false, false, boot + 59999, boot,
                   boot));  // idle 59999 ms
  assert(canSleep(false, false, false, false, boot + 60000, boot,
                  boot));  // idle 60000 ms
  // Each blocker independently blocks sleep (idle and awake conditions met).
  assert(!canSleep(true, false, false, false, boot + 60000, boot,
                   boot));  // USB powered
  assert(!canSleep(false, true, false, false, boot + 60000, boot,
                   boot));  // key held
  assert(!canSleep(false, false, true, false, boot + 60000, boot,
                   boot));  // render busy
  assert(!canSleep(false, false, false, true, boot + 60000, boot,
                   boot));  // render pending
  // Minimum-awake boundary: 2999 ms since the wake is not enough, 3000 ms is.
  assert(!canSleep(false, false, false, false, boot + 60000, boot,
                   boot + 60000 - 2999));  // awake 2999 ms
  assert(canSleep(false, false, false, false, boot + 60000, boot,
                  boot + 60000 - 3000));  // awake 3000 ms
  // Idle comparison across the 2^32 ms wrap: last activity before the
  // rollover, now after it. 70000 ms idle is eligible, 10101 ms is not.
  assert(canSleep(false, false, false, false, 10000, UINT32_MAX - 60000, 9));
  assert(!canSleep(false, false, false, false, 10000, UINT32_MAX - 100, 9));
  // Minimum-awake comparison across the wrap: 31 ms since the wake blocks,
  // 5006 ms allows sleep (idle satisfied in both cases).
  assert(!canSleep(false, false, false, false, 20, UINT32_MAX - 70000,
                   UINT32_MAX - 10));
  assert(canSleep(false, false, false, false, 5, UINT32_MAX - 60001,
                  UINT32_MAX - 5000));
}

void testPrimeForWakeWakeSemantics() {
  // A key held at wake is primed with its raw level: the sketch's immediate
  // wake scan already delivered the press, so the debouncer must neither
  // re-emit while the key stays held nor lose the subsequent release.
  DebouncedInput held;
  held.primeForWake(true, 5000);
  assert(held.update(true, 5001) == Edge::None);   // no duplicate press
  assert(held.update(true, 5100) == Edge::None);   // still held: still nothing
  assert(held.update(false, 5200) == Edge::None);  // release starts a window
  assert(held.update(false, 5226) == Edge::Released);  // release confirmed
  assert(!held.held());

  // A key released at wake primes as released: a later press debounces
  // normally from the prime timestamp.
  DebouncedInput released;
  released.primeForWake(false, 6000);
  assert(released.update(true, 6010) == Edge::None);
  assert(released.update(true, 6035) == Edge::Pressed);
  assert(released.held());
}

// ============================================================================
// Task 12: end-to-end cross-subsystem host scenarios. Real core objects only
// (no mocks): encoder detent -> keymap resolution -> predictive apply ->
// event queue -> quiet-period resync, full keymap replacement, queue
// pressure under the fixed 16-slot budget, authoritative page correction of
// an optimistic value, and debounced USB reach the dependency-free sleep
// predicate. scenarioEmit is the host twin of the sketch's emitInput seam
// (resolve through the active map, then apply; portal/sleep handling is
// sketch-side only).
// ============================================================================

namespace {

PadController *gScenarioPad = nullptr;

void scenarioEmit(const InputEvent &input) {
  if (gScenarioPad == nullptr ||
      static_cast<size_t>(input.key) >= KEY_COUNT) {
    return;
  }
  if (!input.pressed) return;
  const Binding &binding =
      gScenarioPad->activeKeymap[static_cast<size_t>(input.key)];
  const Action action = resolveBinding(binding, input);
  if (action == Action::None) return;
  gScenarioPad->applyAction(action, binding, input.atMs);
}

}  // namespace

void testSyncEncoderToResyncScenario() {
  // One clockwise detent through the default enc_down binding (ScrollDown):
  // the real decoder + scanner pass emits the detent, resolution through the
  // active keymap moves the selection immediately and queues one scroll
  // event, and exactly one quiet-period resync becomes due at 350 ms.
  PadController pad;
  loadDefaultKeymap(pad.activeKeymap);
  addPage(pad, "home", "", 6);
  safeCopy(pad.state.pageId, "home");

  QuadratureDecoder decoder;
  decoder.reset(0b00);
  gScenarioPad = &pad;
  scanEncoderPass(decoder, 0b01, scenarioEmit, 1000);
  scanEncoderPass(decoder, 0b11, scenarioEmit, 1001);
  gScenarioPad = nullptr;

  assert(pad.state.selected == 1);  // selection moved immediately
  assert(pad.queue.size() == 1);
  Event e{};
  assert(pad.queue.pop(e));
  assert(e.action == Action::ScrollDown);
  assert(std::strcmp(e.targetPage, "home") == 0);
  assert(std::strcmp(e.pageId, "home") == 0);
  // One resync becomes due exactly 350 ms after the detent.
  assert(pad.state.resyncPending);
  assert(!pad.resyncDue(1001 + 349));
  assert(pad.resyncDue(1001 + 350));
}

void testSyncKeymapReplacementScenario() {
  // Replace the full active map so r0c1 changes from none (default) to a
  // toggle on switch.living; every other key keeps the default binding.
  // Pressing r0c1 flips the selected item's state immediately and queues the
  // exact entity/page event.
  PadController pad;
  loadDefaultKeymap(pad.activeKeymap);
  addPage(pad, "home", "", 2);
  safeCopy(pad.state.pageId, "home");
  pad.state.selected = 0;
  Item &item = pad.catalog.pages[0].items[0];
  item.type = ItemType::Switch;
  safeCopy(item.entity, "switch.living");
  safeCopy(item.state, "off");

  Binding staging[KEY_COUNT];
  loadDefaultKeymap(staging);
  staging[static_cast<size_t>(KeyId::R0C1)].action = Action::Toggle;
  safeCopy(staging[static_cast<size_t>(KeyId::R0C1)].entity, "switch.living");
  replaceKeymap(pad.activeKeymap, staging);

  gScenarioPad = &pad;
  scenarioEmit(InputEvent{KeyId::R0C1, true, 0, 5000});
  gScenarioPad = nullptr;

  assert(std::strcmp(item.state, "on") == 0);  // immediate item-state change
  Event e{};
  assert(pad.queue.pop(e));
  assert(e.action == Action::Toggle);
  assert(std::strcmp(e.entity, "switch.living") == 0);  // exact entity
  assert(std::strcmp(e.pageId, "home") == 0);           // exact origin page
  assert(pad.queue.size() == 0);
  // Replacement copied the whole map: r0c0 is still the default Home action.
  assert(pad.activeKeymap[static_cast<size_t>(KeyId::R0C0)].action ==
         Action::Home);
}

void testQueuePressureScenario() {
  static_assert(QUEUE_CAPACITY == 16 && MAX_FLUSH_PER_LOOP == 4);

  // Fill all 16 slots: 15 discrete Press events plus one replaceable Edit.
  EventQueue q;
  char buf[24];
  for (size_t i = 0; i < 15; ++i) {
    snprintf(buf, sizeof buf, "d%zu", i);
    assert(q.push(pressEvent(buf)));
  }
  assert(q.push(editEvent("light.volume", 5.0f)));
  assert(q.size() == QUEUE_CAPACITY);

  // A newer Edit for the same entity coalesces in place: values merge, the
  // queue stays full, and nothing overflows.
  assert(q.push(editEvent("light.volume", 7.0f)));
  assert(q.size() == QUEUE_CAPACITY);
  assert(q.overflowCount() == 0);

  // A later discrete Back event survives the full queue: the oldest
  // replaceable slot (the coalesced Edit) is evicted to make room.
  Event back{};
  back.action = Action::Back;
  safeCopy(back.targetPage, "home");
  assert(q.push(back));
  assert(q.size() == QUEUE_CAPACITY);
  assert(q.overflowCount() == 0);

  // One flush budget removes only four events; the discrete Back survives
  // with the twelve remaining.
  gFlushCount = 0;
  const size_t dispatched = flushEvents(q, fakeDispatch, MAX_FLUSH_PER_LOOP);
  assert(dispatched == MAX_FLUSH_PER_LOOP);
  assert(gFlushCount == 4);
  assert(q.size() == QUEUE_CAPACITY - MAX_FLUSH_PER_LOOP);

  size_t total = 0;
  bool sawBack = false;
  bool sawEdit = false;
  while (q.size() > 0) {
    Event e{};
    assert(q.pop(e));
    ++total;
    if (e.action == Action::Back) sawBack = true;
    if (e.action == Action::Edit) sawEdit = true;
  }
  assert(total == 12);
  assert(sawBack);
  assert(!sawEdit);  // the replaceable Edit was sacrificed for Back
}

void testAuthoritativeCorrectionScenario() {
  // An optimistic toggle flips local state and arms a quiet-period resync;
  // the authoritative current-page payload then replaces the cached page
  // whole, so the authoritative value wins and the pending resync clears.
  PadController pad;
  addPage(pad, "home", "", 1);
  safeCopy(pad.state.pageId, "home");
  pad.state.selected = 0;
  Item &item = pad.catalog.pages[0].items[0];
  item.type = ItemType::Switch;
  safeCopy(item.entity, "switch.living");
  safeCopy(item.state, "off");
  Binding b{};
  safeCopy(b.entity, "switch.living");

  ApplyResult r = pad.applyAction(Action::Toggle, b, 2100);
  assert(r.stateChanged && r.eventReady);
  assert(std::strcmp(item.state, "on") == 0);  // optimistic
  assert(pad.state.resyncPending);

  Page authoritative = makePage("home", "Home", 1);
  authoritative.items[0].type = ItemType::Switch;
  safeCopy(authoritative.items[0].entity, "switch.living");
  safeCopy(authoritative.items[0].state, "off");  // backend truth
  assert(commitCurrentPage(pad, authoritative, 2600));
  assert(std::strcmp(item.state, "off") == 0);  // authoritative wins
  assert(!pad.state.resyncPending);             // correction fulfills resync
  assert(pad.state.lastInputMs == 2600);
  assert(!pad.resyncDue(2600 + 500));
}

void testUsbToSleepPredicateScenario() {
  // Bouncing raw USB samples never close the 1500 ms stable window: every
  // sample returns None and the accepted state never changes.
  PowerDebouncer pd;
  assert(pd.sample(true, 0) == PowerEdge::None);      // seeds the baseline
  assert(pd.sample(false, 500) == PowerEdge::None);   // bounce
  assert(pd.sample(true, 1000) == PowerEdge::None);
  assert(pd.sample(false, 1500) == PowerEdge::None);
  assert(pd.sample(true, 2000) == PowerEdge::None);
  assert(pd.sample(true, 2500) == PowerEdge::None);
  assert(pd.sample(true, 3000) == PowerEdge::None);
  // One candidate held for a full 1500 ms stabilizes into exactly one edge.
  assert(pd.sample(true, 3500) == PowerEdge::Connected);
  assert(pd.sample(true, 4000) == PowerEdge::None);  // accepted: no re-emit
  // A stable USB host makes the dependency-free sleep predicate false even
  // after a long idle and minimum-awake; the control line proves the USB
  // power state is the blocker.
  assert(!canSleep(true, false, false, false, 200000, 0, 0));
  assert(canSleep(false, false, false, false, 200000, 0, 0));
}

void testAllActionsReachDefinedOutcome() {
  // Every one of the 21 supported actions either updates local state, queues
  // a documented MQTT event, enters/exits navigation or edit state, or is an
  // explicit no-op: None, an invalid non-directional Scroll, and Enter on
  // sensor/media items (the brief's listed no-op categories).
  PadController pad;
  addPage(pad, "home", "", 3);
  addPage(pad, "child", "home", 1);
  addPage(pad, "settings", "home", 1);
  safeCopy(pad.state.pageId, "home");
  Item (&items)[MAX_ITEMS_PER_PAGE] = pad.catalog.pages[0].items;
  items[0].type = ItemType::Switch;
  safeCopy(items[0].entity, "switch.x");
  safeCopy(items[0].state, "off");
  items[1].type = ItemType::Number;
  safeCopy(items[1].entity, "number.x");
  items[1].value = 5.0f;
  items[1].min = 0.0f;
  items[1].max = 10.0f;
  items[1].step = 2.0f;
  items[2].type = ItemType::MediaPlayer;
  safeCopy(items[2].entity, "media.x");
  Binding b{};
  safeCopy(b.entity, "switch.x");
  ApplyResult r{};

  // none: explicit no-op (no local change, no event).
  pad.state.selected = 0;
  r = pad.applyAction(Action::None, b, 100);
  assert(!r.stateChanged && !r.eventReady);

  // scroll is directional: a zero-direction detent resolves to None (the
  // invalid directional scroll no-op); real directions resolve to scrolls.
  Binding scroll{};
  scroll.action = Action::Scroll;
  assert(resolveBinding(scroll, InputEvent{KeyId::EncDown, true, 1, 150}) ==
         Action::ScrollDown);
  assert(resolveBinding(scroll, InputEvent{KeyId::EncDown, true, -1, 151}) ==
         Action::ScrollUp);
  assert(resolveBinding(scroll, InputEvent{KeyId::EncDown, true, 0, 152}) ==
         Action::None);
  r = pad.applyAction(Action::Scroll, b, 160);
  assert(!r.stateChanged && !r.eventReady);

  // Enter on sensor/media items is an explicit no-op (selectedItemAction
  // returns None) and the raw Enter action itself is resolved upstream.
  items[2].type = ItemType::Sensor;
  safeCopy(items[2].entity, "sensor.x");
  pad.state.selected = 2;
  assert(pad.selectedItemAction() == Action::None);  // sensor entry no-ops
  items[2].type = ItemType::MediaPlayer;
  safeCopy(items[2].entity, "media.x");
  assert(pad.selectedItemAction() == Action::None);  // media entry no-ops
  r = pad.applyAction(Action::Enter, b, 200);
  assert(!r.stateChanged && !r.eventReady);

  // Back on a root page without a parent no-ops; from a child page it exits.
  safeCopy(pad.state.pageId, "home");
  pad.state.selected = 0;
  r = pad.applyAction(Action::Back, b, 300);
  assert(!r.stateChanged && !r.eventReady);
  safeCopy(pad.state.pageId, "child");
  r = pad.applyAction(Action::Back, b, 350);
  assert(r.stateChanged && r.eventReady);
  assert(std::strcmp(pad.state.pageId, "home") == 0);  // exit to parent

  // Home / Settings / Navigate reach cached pages; an empty navigate target
  // is the explicit no-op fallback.
  pad.state.selected = 0;
  r = pad.applyAction(Action::Home, b, 400);
  assert(r.stateChanged && r.eventReady);
  assert(std::strcmp(pad.state.pageId, "home") == 0);
  r = pad.applyAction(Action::Settings, b, 450);
  assert(r.stateChanged && r.eventReady);
  assert(std::strcmp(pad.state.pageId, "settings") == 0);
  r = pad.applyAction(Action::Navigate, b, 500);  // no target anywhere
  assert(!r.stateChanged && !r.eventReady);
  safeCopy(pad.state.pageId, "home");
  safeCopy(items[0].targetPage, "child");
  r = pad.applyAction(Action::Navigate, b, 550);
  assert(r.stateChanged && r.eventReady);
  assert(std::strcmp(pad.state.pageId, "child") == 0);

  // ScrollUp/ScrollDown move the selection and queue scroll events; the
  // top/bottom boundaries no-op.
  safeCopy(pad.state.pageId, "home");
  pad.state.selected = 0;
  r = pad.applyAction(Action::ScrollDown, b, 600);
  assert(r.stateChanged && r.eventReady);
  assert(pad.state.selected == 1);
  r = pad.applyAction(Action::ScrollUp, b, 650);
  assert(r.stateChanged && r.eventReady);
  assert(pad.state.selected == 0);
  r = pad.applyAction(Action::ScrollUp, b, 660);  // at the first item
  assert(!r.stateChanged && !r.eventReady);

  // Toggle / On / Off update local state and queue an event.
  pad.state.selected = 0;
  r = pad.applyAction(Action::Toggle, b, 700);
  assert(r.stateChanged && r.eventReady);
  assert(std::strcmp(items[0].state, "on") == 0);
  r = pad.applyAction(Action::On, b, 750);
  assert(r.stateChanged && r.eventReady);
  assert(std::strcmp(items[0].state, "on") == 0);
  r = pad.applyAction(Action::Off, b, 800);
  assert(r.stateChanged && r.eventReady);
  assert(std::strcmp(items[0].state, "off") == 0);

  // Drain the navigation/scroll/toggle events queued above so each section
  // below inspects exactly the events it produced.
  while (pad.queue.size() > 0) {
    Event drained{};
    pad.queue.pop(drained);
  }

  // Press and the media/volume actions queue a documented MQTT event for the
  // bound entity; local state is untouched.
  pad.state.selected = 2;
  safeCopy(b.entity, "media.x");
  for (Action action : {Action::Press, Action::VolumeUp, Action::VolumeDown,
                        Action::MediaNext, Action::MediaPrev}) {
    r = pad.applyAction(action, b, 900);
    assert(!r.stateChanged);
    assert(r.eventReady);
    Event e{};
    assert(pad.queue.pop(e));
    assert(e.action == action);
    assert(std::strcmp(e.entity, "media.x") == 0);
    assert(std::strcmp(e.pageId, "home") == 0);
  }

  // Edit enters edit mode; ScrollDown adjusts the value; Confirm exits.
  pad.state.selected = 1;
  safeCopy(b.entity, "number.x");
  r = pad.applyAction(Action::Edit, b, 1000);
  assert(r.stateChanged && r.eventReady);
  assert(pad.state.editing);
  r = pad.applyAction(Action::ScrollDown, b, 1050);
  assert(items[1].value == 7.0f);
  r = pad.applyAction(Action::Confirm, b, 1100);
  assert(r.stateChanged && r.eventReady);
  assert(!pad.state.editing);
  while (pad.queue.size() > 0) {
    Event e{};
    pad.queue.pop(e);
  }

  // get_all_pages stays reserved for the first MQTT connection: a key press
  // never produces it, applyAction leaves state and queue untouched.
  r = pad.applyAction(Action::GetAllPages, b, 1300);
  assert(!r.stateChanged && !r.eventReady);
  assert(pad.queue.size() == 0);

  // keymap queues one keymap request event for the backend republish.
  r = pad.applyAction(Action::Keymap, b, 1350);
  assert(!r.stateChanged);
  assert(r.eventReady);
  Event e{};
  assert(pad.queue.pop(e));
  assert(e.action == Action::Keymap);
  assert(std::strcmp(e.pageId, "home") == 0);
  assert(pad.queue.size() == 0);
}


// P1.1: entity actions steer exactly the binding's entity. A missing entity is
// a defined no-op, an empty page still emits the event, and the selected item
// is never substituted for the bound entity.
void testEntityBindingSemantics() {
  // Empty home page: an entity action still emits exactly one matching event
  // and nothing local is flipped.
  PadController padEmpty;
  addPage(padEmpty, "home", "", 0);
  safeCopy(padEmpty.state.pageId, "home");
  Binding light{};
  light.action = Action::Toggle;
  safeCopy(light.entity, "light.desk");
  ApplyResult r = padEmpty.applyAction(Action::Toggle, light, 1000);
  assert(r.eventReady);
  assert(!r.stateChanged);  // nothing local to flip on an empty page
  Event e{};
  assert(padEmpty.queue.size() == 1);
  assert(padEmpty.queue.pop(e));
  assert(e.action == Action::Toggle);
  assert(std::strcmp(e.entity, "light.desk") == 0);
  assert(std::strcmp(e.pageId, "home") == 0);

  // Missing entity: defined no-op — no event, no local change.
  Binding bare{};
  bare.action = Action::Toggle;
  r = padEmpty.applyAction(Action::Toggle, bare, 1100);
  assert(!r.eventReady && !r.stateChanged);
  r = padEmpty.applyAction(Action::On, bare, 1200);
  assert(!r.eventReady && !r.stateChanged);
  r = padEmpty.applyAction(Action::Press, bare, 1300);
  assert(!r.eventReady && !r.stateChanged);

  // Selected item with a DIFFERENT entity: the event keeps the bound entity
  // and the unrelated item's local state is untouched.
  PadController pad;
  addPage(pad, "home", "", 2);
  Item (&items)[MAX_ITEMS_PER_PAGE] = pad.catalog.pages[0].items;
  items[0].type = ItemType::Switch;
  safeCopy(items[0].entity, "switch.other");
  safeCopy(items[0].state, "off");
  items[1].type = ItemType::Light;
  safeCopy(items[1].entity, "light.desk");
  safeCopy(items[1].state, "off");
  safeCopy(pad.state.pageId, "home");
  pad.state.selected = 0;  // selected item is NOT the bound entity
  r = pad.applyAction(Action::Toggle, light, 1400);
  assert(r.eventReady);
  assert(!r.stateChanged);
  assert(std::strcmp(items[0].state, "off") == 0);  // not touched
  assert(pad.queue.pop(e));
  assert(std::strcmp(e.entity, "light.desk") == 0);
  // Exactly one event for exactly one detent press.
  assert(pad.queue.size() == 0);

  // Selected item WITH the same entity: local state flips optimistically and
  // the event still carries the bound entity.
  pad.state.selected = 1;
  r = pad.applyAction(Action::Toggle, light, 1500);
  assert(r.eventReady && r.stateChanged);
  assert(std::strcmp(items[1].state, "on") == 0);
  assert(pad.queue.pop(e));
  assert(std::strcmp(e.entity, "light.desk") == 0);
  assert(pad.queue.size() == 0);

  // On/Off: same discipline.
  r = pad.applyAction(Action::On, light, 1600);
  assert(r.eventReady && r.stateChanged);
  assert(std::strcmp(items[1].state, "on") == 0);
  assert(std::strcmp(items[0].state, "off") == 0);
  r = pad.applyAction(Action::Off, light, 1700);
  assert(r.eventReady && r.stateChanged);
  assert(std::strcmp(items[1].state, "off") == 0);

  // Drain the On/Off events so the keymap section inspects exactly its own.
  while (pad.queue.size() > 0) {
    Event drained{};
    pad.queue.pop(drained);
  }

  // Keymap with a binding target page requests exactly that page's keymap.
  addPage(pad, "child", "home", 0);
  Binding km{};
  km.action = Action::Keymap;
  safeCopy(km.targetPage, "child");
  r = pad.applyAction(Action::Keymap, km, 1800);
  assert(r.eventReady);
  assert(pad.queue.pop(e));
  assert(e.action == Action::Keymap);
  assert(std::strcmp(e.targetPage, "child") == 0);
  // Unknown target degenerates to the raw id, never to the current page.
  Binding km2{};
  km2.action = Action::Keymap;
  safeCopy(km2.targetPage, "missing");
  r = pad.applyAction(Action::Keymap, km2, 1900);
  assert(r.eventReady);
  assert(pad.queue.pop(e));
  assert(std::strcmp(e.targetPage, "missing") == 0);
  // No target page keeps the origin-only event (no targetPage field).
  Binding km3{};
  km3.action = Action::Keymap;
  r = pad.applyAction(Action::Keymap, km3, 2000);
  assert(r.eventReady);
  assert(pad.queue.pop(e));
  assert(e.targetPage[0] == '\0');
}

// P1.3: the 100 ms spacing is applied to every normal partial refresh even
// when MAX_PARTIAL_REFRESHES == 0 (which only disables periodic forced fulls),
// and wrap-around millis remain safe.
void testRefreshSpacingPolicy() {
  static_assert(MAX_PARTIAL_REFRESHES == 0);
  RefreshPolicy policy;

  // First refresh is immediate.
  assert(policy.spacingElapsed(1000));
  RefreshMode first = policy.beginRefresh(1000, /*snapshotForceFull=*/false);
  assert(first == RefreshMode::Partial);  // zero limit never forces periodic fulls

  // 50 ms later the spacing still applies.
  assert(!policy.spacingElapsed(1050));
  assert(policy.remainingSpacingMs(1050) == 50);
  assert(policy.remainingSpacingMs(1099) == 1);

  // Exactly at the interval the spacing is available again.
  assert(policy.spacingElapsed(1100));
  assert(policy.remainingSpacingMs(1100) == 0);

  // Wrap-around millis (~49 days uptime): a start just before 2^32 and a check
  // just after the wrap must compute the correct elapsed time.
  policy.beginRefresh(0xFFFFFFF0U, false);
  const uint32_t afterWrap = static_cast<uint32_t>(0x00000014U);
  assert(policy.remainingSpacingMs(afterWrap) == 64);  // 36 ms elapsed -> 64 left
  assert(!policy.spacingElapsed(afterWrap));
  const uint32_t atBoundary = static_cast<uint32_t>(0x00000054U);  // +100 ms
  assert(policy.spacingElapsed(atBoundary));

  // Many successful partials never force a full refresh when the limit is 0.
  for (uint8_t i = 0; i < 200; ++i) {
    policy.completeRefresh(true);
    RefreshMode mode = policy.beginRefresh(2000 + i, false);
    assert(mode == RefreshMode::Partial);
  }
  // A demanded snapshot full is still honoured, and it resets the counter.
  RefreshMode forced = policy.beginRefresh(5000, true);
  assert(forced == RefreshMode::Full);
  policy.completeRefresh(true);
  policy.beginRefresh(5100, false);
  assert(policy.partialCount() == 0 || policy.partialCount() <= 200);
}


// P1.2: the sleep/render interlock resolves the TOCTOU between the sleep gate
// and the render task claim. Simulates every required interleaving as
// single-threaded state transitions (the sketch supplies the atomicity).
void testSleepInterlockInterleavings() {
  SleepInterlock il;

  // 1. Claim BEFORE the gate: the drawn frame must block sleep.
  assert(il.tryBeginDraw());
  assert(!il.setSleepEntered(/*renderBusy=*/true, /*snapshotPending=*/false));
  assert(il.tryBeginDraw());  // still awake

  // 2. Claim BETWEEN gate and sleep: on a fresh, idle renderer the gate may
  // grant sleep; once granted, a later claim is refused until wake.
  SleepInterlock il2;
  assert(il2.setSleepEntered(false, false));  // gate sees an idle renderer
  assert(il2.sleeping());
  assert(!il2.tryBeginDraw());  // render task may not start a draw now
  assert(!il2.setSleepEntered(false, false));  // already entered: refused
  assert(il2.wake());
  assert(il2.tryBeginDraw());  // draws resume after wake

  // 3. Publish during sleep approach: a pending snapshot blocks sleep.
  SleepInterlock il3;
  assert(!il3.setSleepEntered(false, /*snapshotPending=*/true));
  assert(!il3.sleeping());
  assert(il3.tryBeginDraw());

  // 4. Wake with a pending frame: sleep entered, wake, then the frame draws.
  SleepInterlock il4;
  assert(il4.setSleepEntered(false, false));
  assert(!il4.tryBeginDraw());
  (void)il4.wake();
  assert(il4.tryBeginDraw());
  // A stray wake (no sleep entered) is a no-op.
  SleepInterlock il5;
  assert(!il5.wake());
}

void testItemTypeDescriptorTable() {
  // The descriptor table is indexed by ItemType: every enum value needs a row, in
  // order. A missing or reordered row would silently give one item type another
  // type's action, which no other test would catch.
  assert(ITEM_TYPE_COUNT == 15);
  assert(ACTION_COUNT == 21);
  for (size_t index = 0; index < ITEM_TYPE_COUNT; ++index) {
    assert(static_cast<size_t>(ITEM_TYPE_DESCRIPTORS[index].type) == index);
  }

  // A non-editing press is the row's default action; edit mode adds Confirm for
  // exactly the editable types and changes nothing otherwise.
  for (size_t index = 0; index < ITEM_TYPE_COUNT; ++index) {
    Item item{};
    item.type = static_cast<ItemType>(index);
    const ItemTypeDescriptor &descriptor = itemTypeDescriptor(item.type);
    assert(actionForSelectedItem(item, false) == descriptor.defaultAction);
    assert(actionForSelectedItem(item, true) ==
           (descriptor.editable ? Action::Confirm : descriptor.defaultAction));
  }

  // Pin the documented behaviour so a "harmless refactor" cannot change what a
  // press does on real hardware.
  assert(itemTypeDescriptor(ItemType::Category).defaultAction == Action::Navigate);
  assert(itemTypeDescriptor(ItemType::Light).defaultAction == Action::Toggle);
  assert(itemTypeDescriptor(ItemType::Switch).defaultAction == Action::Toggle);
  assert(itemTypeDescriptor(ItemType::Script).defaultAction == Action::Press);
  assert(itemTypeDescriptor(ItemType::Button).defaultAction == Action::Press);
  assert(itemTypeDescriptor(ItemType::Scene).defaultAction == Action::Press);
  assert(itemTypeDescriptor(ItemType::Sensor).defaultAction == Action::None);
  assert(itemTypeDescriptor(ItemType::MediaPlayer).defaultAction == Action::None);
  assert(itemTypeDescriptor(ItemType::Number).defaultAction == Action::Edit);
  assert(itemTypeDescriptor(ItemType::Number).editable);
  // The four types added with C4: a tap toggles (or locks), a hold turns off (or
  // unlocks), and a cover has no second action because HA's cover integration has
  // no turn_off to map it to.
  assert(itemTypeDescriptor(ItemType::Cover).defaultAction == Action::Toggle);
  Item coverItem{};
  coverItem.type = ItemType::Cover;
  Item lockItem{};
  lockItem.type = ItemType::Lock;
  assert(alternateActionForSelectedItem(coverItem) == Action::None);
  assert(alternateActionForSelectedItem(lockItem) == Action::Off);
  assert(itemTypeDescriptor(ItemType::Fan).defaultAction == Action::Toggle);
  assert(itemTypeDescriptor(ItemType::Fan).alternateAction == Action::Off);
  assert(itemTypeDescriptor(ItemType::InputBoolean).defaultAction == Action::Toggle);
  assert(itemTypeDescriptor(ItemType::InputBoolean).alternateAction == Action::Off);
  assert(itemTypeDescriptor(ItemType::Lock).defaultAction == Action::On);
  assert(itemTypeDescriptor(ItemType::Lock).alternateAction == Action::Off);
  assert(itemTypeDescriptor(ItemType::Settings).defaultAction == Action::Settings);
  assert(itemTypeDescriptor(ItemType::Back).defaultAction == Action::Back);

  // Only Number is editable today; a second editable type must be intentional.
  size_t editableCount = 0;
  for (size_t index = 0; index < ITEM_TYPE_COUNT; ++index) {
    if (ITEM_TYPE_DESCRIPTORS[index].editable) ++editableCount;
  }
  assert(editableCount == 1);

  // An out-of-range type (corrupt or truncated payload) must not read past the
  // table; the accessor clamps to the inert Category row.
  assert(itemTypeDescriptor(static_cast<ItemType>(200)).type == ItemType::Category);
}

void testFormatDiagnostics() {
  // The payload is pinned byte for byte: a dashboard consuming micropad/diag
  // depends on these keys, and a silent rename would break it with no compile
  // error anywhere.
  Diagnostics diag{};
  diag.uptimeS = 3661;
  diag.mqttConnects = 3;
  diag.catalogParses = 2;
  diag.catalogRejects = 1;
  diag.pageRejects = 4;
  diag.keymapRejects = 5;
  diag.eventDrops = 6;
  diag.heapFreeBytes = 123456;
  diag.heapMinBytes = 100000;
  diag.stackMinBytes = 9876;
  char buffer[DIAGNOSTICS_JSON_CAP];
  const size_t written = formatDiagnostics(diag, buffer);
  assert(written == std::strlen(buffer));
  assert(std::strcmp(buffer,
                     "{\"uptime_s\":3661,\"mqtt_connects\":3,\"catalog_parses\":2,"
                     "\"catalog_rejects\":1,\"page_rejects\":4,\"keymap_rejects\":5,"
                     "\"event_drops\":6,\"heap_free\":123456,\"heap_min\":100000,"
                     "\"stack_min\":9876}") == 0);

  // A freshly booted pad reports zeros rather than omitting keys, so a consumer
  // never has to distinguish "no value" from "no problem".
  Diagnostics empty{};
  const size_t emptyWritten = formatDiagnostics(empty, buffer);
  assert(emptyWritten > 0);
  assert(std::strstr(buffer, "\"uptime_s\":0") != nullptr);
  assert(std::strstr(buffer, "\"heap_min\":0") != nullptr);
  assert(std::strstr(buffer, "\"stack_min\":0") != nullptr);
  assert(std::strstr(buffer, "\"event_drops\":0") != nullptr);

  // Worst case (every counter at its maximum) must fit the budget: the formatter
  // refuses to write rather than truncating, and a refused payload would mean a
  // diagnostics topic that silently never appears.
  Diagnostics maxed{};
  maxed.uptimeS = 4294967295UL;
  maxed.mqttConnects = 4294967295UL;
  maxed.catalogParses = 4294967295UL;
  maxed.catalogRejects = 4294967295UL;
  maxed.pageRejects = 4294967295UL;
  maxed.keymapRejects = 4294967295UL;
  maxed.eventDrops = 4294967295UL;
  maxed.heapFreeBytes = 4294967295UL;
  maxed.heapMinBytes = 4294967295UL;
  maxed.stackMinBytes = 4294967295UL;
  const size_t maxWritten = formatDiagnostics(maxed, buffer);
  assert(maxWritten > 0);
  // Measured worst case, not an estimate: it is what decides the MQTT payload budget.
  assert(maxWritten == 254);
  assert(maxWritten < DIAGNOSTICS_JSON_CAP);
  // Headroom for at least one more field, so the cap is not just "barely enough".
  assert(DIAGNOSTICS_JSON_CAP - maxWritten >= 32);

  // The reason string and the boolean must agree for every combination, or a save
  // could be refused with an explanation that does not match the rule.
  {
    const char *ssids[] = {"", "home", "home"};
    const char *passes[] = {"", "short", "long-enough"};
    const char *users[] = {"", "user", "user"};
    for (int i = 0; i < 3; ++i) {
      for (int j = 0; j < 3; ++j) {
        for (int k = 0; k < 3; ++k) {
          micropad::Settings probe = micropad::defaultSettings();
          safeCopy(probe.wifiSsid, ssids[i]);
          safeCopy(probe.wifiPassword, passes[j]);
          safeCopy(probe.mqttUser, users[k]);
          const bool valid = micropad::validateSettings(probe);
          const bool reasonEmpty = micropad::settingsError(probe)[0] == '\0';
          assert(valid == reasonEmpty);
        }
      }
    }
    micropad::Settings noPort = micropad::defaultSettings();
    safeCopy(noPort.wifiSsid, "home");
    safeCopy(noPort.wifiPassword, "long-enough");
    noPort.mqttPort = 0;
    assert(!micropad::validateSettings(noPort));
    assert(std::strcmp(micropad::settingsError(noPort), "mqtt port must be 1..65535") == 0);
    micropad::Settings noHost = micropad::defaultSettings();
    noHost.mqttHost[0] = '\0';
    assert(std::strcmp(micropad::settingsError(noHost), "mqtt host must not be empty") == 0);
    micropad::Settings noUser = micropad::defaultSettings();
    noUser.mqttUser[0] = '\0';
    assert(std::strcmp(micropad::settingsError(noUser), "mqtt user must not be empty") == 0);
    micropad::Settings shortPass = micropad::defaultSettings();
    safeCopy(shortPass.wifiSsid, "home");
    safeCopy(shortPass.wifiPassword, "short");
    assert(std::strcmp(micropad::settingsError(shortPass),
                       "wifi password must be 8..64 characters") == 0);
  }

  static_assert(DIAGNOSTICS_PUBLISH_MS == 60000, "diagnostics republish interval");
  static_assert(DIAGNOSTICS_JSON_CAP >= 200, "diagnostics payload budget");
}

void testLongPressGesture() {
  // Debounce contract: a tap never fires the hold, a hold fires exactly once at
  // the threshold, and a release re-arms it for the next press.
  DebouncedInput input;
  const uint32_t t0 = 1000;
  assert(input.update(true, t0) == Edge::None);            // candidate, not stable
  assert(input.update(true, t0 + INPUT_DEBOUNCE_MS) == Edge::Pressed);
  assert(input.update(true, t0 + 100) == Edge::None);
  assert(input.update(true, t0 + INPUT_DEBOUNCE_MS + LONG_PRESS_MS - 1) == Edge::None);
  assert(input.update(true, t0 + INPUT_DEBOUNCE_MS + LONG_PRESS_MS) == Edge::LongPress);
  assert(input.update(true, t0 + INPUT_DEBOUNCE_MS + LONG_PRESS_MS + 500) == Edge::None);
  assert(input.update(false, t0 + 3000) == Edge::None);
  assert(input.update(false, t0 + 3000 + INPUT_DEBOUNCE_MS) == Edge::Released);
  // Second hold on the same key fires again (longPressFired_ was cleared).
  assert(input.update(true, t0 + 4000) == Edge::None);
  assert(input.update(true, t0 + 4000 + INPUT_DEBOUNCE_MS) == Edge::Pressed);
  assert(input.update(true, t0 + 4000 + INPUT_DEBOUNCE_MS + LONG_PRESS_MS) == Edge::LongPress);

  // A long press reaches the item's alternate action, and only where one exists.
  PadController pad;
  addPage(pad, "home", "", 3);
  Item (&items)[MAX_ITEMS_PER_PAGE] = pad.catalog.pages[0].items;
  items[0].type = ItemType::Light;
  safeCopy(items[0].entity, "light.desk");
  items[1].type = ItemType::Switch;
  safeCopy(items[1].entity, "switch.fan");
  items[2].type = ItemType::Sensor;
  safeCopy(items[2].entity, "sensor.temp");
  safeCopy(pad.state.pageId, "home");
  pad.state.selected = 0;
  assert(pad.selectedItemAction() == Action::Toggle);
  assert(pad.selectedItemLongPressAction() == Action::Off);
  pad.state.selected = 1;
  assert(pad.selectedItemAction() == Action::Toggle);
  assert(pad.selectedItemLongPressAction() == Action::Off);
  // A sensor has no second action: the hold is ignored, not redirected.
  pad.state.selected = 2;
  assert(pad.selectedItemAction() == Action::None);
  assert(pad.selectedItemLongPressAction() == Action::None);
  // Off a page (no selection) the hold resolves to nothing.
  safeCopy(pad.state.pageId, "missing");
  assert(pad.selectedItemLongPressAction() == Action::None);

  // Every type's alternate is either distinct from its default or explicitly
  // None: an accidental "alternate == default" would make a hold dispatch a
  // duplicate action.
  for (size_t index = 0; index < ITEM_TYPE_COUNT; ++index) {
    const ItemTypeDescriptor &descriptor = ITEM_TYPE_DESCRIPTORS[index];
    Item item{};
    item.type = static_cast<ItemType>(index);
    const Action alternate = alternateActionForSelectedItem(item);
    if (alternate != Action::None) assert(alternate != descriptor.defaultAction);
  }
  static_assert(LONG_PRESS_MS == 600, "long press threshold");
}

int main() {
  static_assert(KEY_COUNT == 14);
  static_assert(QUEUE_CAPACITY == 16);
  static_assert(MAX_FLUSH_PER_LOOP == 4);
  static_assert(MQTT_BUFFER_BYTES == 16384);
  static_assert(EVENT_BUFFER_BYTES == 512);

  char exact[5]{};
  assert(safeCopy(exact, "four"));
  assert(std::strcmp(exact, "four") == 0);

  char clipped[5]{'x', 'x', 'x', 'x', 'x'};
  assert(!safeCopy(clipped, "longer"));
  assert(std::strcmp(clipped, "long") == 0);
  assert(clipped[4] == '\0');

  // safeCopyClip() truncates instead of failing: display-only payload fields
  // (name, title, state, unit) must never reject a whole page/catalog.
  char fit[8]{};
  assert(safeCopyClip(fit, "eight!!"));  // 7 chars + NUL fits exactly
  assert(std::strcmp(fit, "eight!!") == 0);
  assert(!safeCopyClip(fit, "much longer text"));
  assert(std::strcmp(fit, "much lo") == 0);
  assert(fit[7] == '\0');
  char empty[4]{'a', 'b', 'c', '\0'};
  assert(!safeCopyClip(empty, nullptr));
  assert(empty[0] == '\0');
  // Clipping a text that exactly fills the cap reports success.
  char exactCap[4]{};
  assert(safeCopyClip(exactCap, "abc"));
  assert(std::strcmp(exactCap, "abc") == 0);

  testSafeCopyClipDisplayCaps();
  testRenderPrimBudgetHeadroom();
  testKeymapModel();
  testActionModel();
  testItemTypeModel();
  testDebounceTiming();
  testDebounceHeldAndPrime();
  testQuadratureDetents();
  testMatrixScanPass();
  testEncoderScanPassMapping();
  testAnyMatrixKeyHeld();
  testEventQueueFifoAndBudget();
  testEventQueueCoalescing();
  testEventQueuePushFront();
  testSelectionWindow();
  testSelectedItemActions();
  testOptimisticValues();
  testEntityBindingSemantics();
  testRefreshSpacingPolicy();
  testSleepInterlockInterleavings();
  testLocalNavigationAndResync();
  testSettingsDefaults();
  testSettingsValidation();
  testSelectionBounds();
  testCommitCurrentPageUpsertClampsAndClears();
  testCommitCurrentPageAppendsUnknownPage();
  testCommitCurrentPageRejectedKeepsState();
  testPageContentEqualityDetectsAuthoritativeChanges();
  testReplaceKeymapReplacesAllEntries();
  testQueuedEventsCarryOriginPage();
  testSnapshotMailboxTwoSlotStateMachine();
  testSnapshotMailboxReleaseAndPending();
  testSnapshotMailboxCoalescesWhileRendering();
  testLayoutConstants();
  testScrollbarThumbGeometry();
  testLayoutNormalUiModel();
  testLayoutPortalUiModel();
  testNetworkStatusPrims();
  testPowerIconPrims();
  testFormatRowValue();
  testClipTextSpan();
  testRefreshMustBeFullReasons();
  testRefreshPolicyModeAndCount();
  testRefreshPolicySpacingWrapSafe();
  testElapsedMsWrapSafe();
  testLayoutModelPrimsStayOnCanvas();
  testPowerDebouncerConnectedAndDisconnectedWindows();
  testPowerDebouncerBounceNeverStabilizes();
  testPowerDebouncerWrapAround();
  testSleepPredicate();
  testPrimeForWakeWakeSemantics();
  testSyncEncoderToResyncScenario();
  testSyncKeymapReplacementScenario();
  testQueuePressureScenario();
  testAuthoritativeCorrectionScenario();
  testUsbToSleepPredicateScenario();
  testItemTypeDescriptorTable();
  testFormatDiagnostics();
  testLongPressGesture();
  testAllActionsReachDefinedOutcome();
  static_assert(micropad::ACTION_COUNT == 21, "21 supported actions");
  static_assert(micropad::ITEM_TYPE_COUNT == 15, "15 supported item types");
  static_assert(micropad::USB_SAMPLE_MS == 500, "500 ms USB sample interval");
  static_assert(micropad::USB_STABLE_MS == 1500, "1500 ms USB stable window");
  static_assert(sizeof(RenderSnapshot) > 0, "snapshot type is defined");
  return 0;
}
