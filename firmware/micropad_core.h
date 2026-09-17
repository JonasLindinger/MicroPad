// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace micropad {

// Shared firmware constants (see "Shared Firmware Interfaces").
constexpr size_t KEY_COUNT = 14;
constexpr size_t QUEUE_CAPACITY = 16;
constexpr size_t MAX_FLUSH_PER_LOOP = 4;
constexpr size_t MAX_PAGES = 24;
constexpr size_t MAX_ITEMS_PER_PAGE = 20;
constexpr size_t VISIBLE_ROWS = 4;
constexpr size_t SETUP_PASSWORD_LENGTH = 16;
constexpr uint32_t INPUT_DEBOUNCE_MS = 25;
// How long a key must stay held before the hold counts as a long press. Chosen
// well above a deliberate tap (a normal press here is 25 ms of debounce plus a
// human tap) so the gesture cannot fire by accident.
constexpr uint32_t LONG_PRESS_MS = 600;
// How long after a press a second press still counts as a double press. A key
// without a bound double gesture is never affected: the filter only defers a tap
// when the effective keymap binds one (see DebouncedInput::setDoublePressEnabled),
// so ordinary keys keep firing on the press edge with no added latency. Well
// below LONG_PRESS_MS, so a deferred tap is always delivered before a hold could
// fire on the same press.
constexpr uint32_t DOUBLE_PRESS_MS = 350;
constexpr uint32_t RESYNC_SETTLE_MS = 350;
constexpr uint32_t WIFI_RETRY_MS = 5000;
// Recovery: a saved Wi-Fi record that can never associate (typo, moved AP,
// changed AP password) must not leave the pad unreachable. The default keymap
// binds no `settings` action and the effective keymap only arrives over MQTT,
// so after this many failed station attempts the pad re-opens its own setup
// portal instead — the Back key (bound by default) still cancels it and leaves
// the stored settings untouched. 24 attempts x WIFI_RETRY_MS = 2 minutes.
constexpr uint32_t WIFI_FAILS_BEFORE_PORTAL = 24;
constexpr uint32_t MQTT_RETRY_MS = 5000;
// A connected broker that never delivers the retained catalog (dropped because
// it exceeded the 16 KiB client buffer, or a lost retained publication) is
// invisible to the client, so the pad re-requests the catalog at this interval
// while it is connected but has no catalog.
constexpr uint32_t CATALOG_RETRY_MS = 60000;
// Setup-portal inactivity safety net (issue #6). The portal used to restart
// the pad after 5 minutes even while the phone was attached and the user was
// actively pairing, because the page HTTP traffic did not reach the activity
// clock. The pad now never restarts while a setup client is connected
// (portalTick gates the restart on softAPgetStationNum() == 0), and this
// client-free window is a generous 30 minutes so a bumped session does not
// lose the pairing flow.
constexpr uint32_t PORTAL_IDLE_MS = 1800000;
constexpr uint32_t IDLE_SLEEP_MS = 60000;
// Sleeping stays disabled (see the loop() comment): the ESP32-S3 wake path is
// still broken, and isPowered() only detects an open CDC data session, so a pad
// on a plain USB cable would be treated as battery-powered and sleep mid-session
// (MQTT keepalive dies, broker drops, pad never reconnects).
constexpr bool LIGHT_SLEEP_ENABLED = false;
constexpr uint32_t MIN_AWAKE_MS = 3000;
constexpr uint32_t USB_SAMPLE_MS = 500;
constexpr uint32_t USB_STABLE_MS = 1500;
// The warm-sleep predicate is sampled at this interval instead of once per
// loop pass: it can only become true after IDLE_SLEEP_MS (60 s) of idleness, so
// 250 ms of extra latency is irrelevant, while re-scanning the whole matrix on
// every pass (anyMatrixKeyHeld) doubled the input I/O of an awake battery pad.
constexpr uint32_t SLEEP_GATE_SAMPLE_MS = 250;
constexpr size_t MQTT_BUFFER_BYTES = 16384;
constexpr size_t EVENT_BUFFER_BYTES = 512;
// Periodic full-panel updates: a full refresh clears residual panel charge
// that makes black pixels slowly drift toward grey after many partial
// updates (hardware-observed ghosting on this panel). After
// MAX_PARTIAL_REFRESHES partial refreshes the next refresh is forced full;
// the visible flash is the accepted trade for staying black.
constexpr uint8_t MAX_PARTIAL_REFRESHES = 10;
constexpr uint32_t MIN_REFRESH_SPACING_MS = 100;
// Loop-task task-WDT deadline (v6-proven 20 s). The default ESP-IDF deadline
// is 5 s with panic; the setup portal's WebServer can block the loop for its
// full socket timeouts while a pairing phone stalls a request, so the shorter
// default panics-reboots the pad during initial setup (issue #6).
constexpr uint32_t TASK_WDT_TIMEOUT_MS = 20000;

// Physical matrix geometry. The first MATRIX_KEY_COUNT KeyId values (r0c0 ..
// r2c3) map directly to (row * MATRIX_COLS + col).
constexpr uint8_t MATRIX_ROWS = 3;
constexpr uint8_t MATRIX_COLS = 4;
constexpr uint8_t MATRIX_KEY_COUNT = MATRIX_ROWS * MATRIX_COLS;

// Bounded string field capacities (see "Fixed Data Model"). Display-only
// fields (title, item name, state, unit) are clipped by safeCopyClip(); the
// identifier fields (page id, entity, target page) stay strict, because a
// clipped identifier would silently address the wrong entity or page. 32
// characters is already far more than the 12-character value column or the
// 22-character title strip can render, so shrinking the caps costs no
// visible information and returns 8 KB of static RAM.
constexpr size_t PAGE_ID_CAP = 33;
constexpr size_t TITLE_CAP = 33;
constexpr size_t ITEM_NAME_CAP = 33;
constexpr size_t ENTITY_CAP = 97;
constexpr size_t STATE_CAP = 33;
constexpr size_t UNIT_CAP = 17;

// Bounded credential fields (see "Persist Validated Device Settings"). Sizes
// match the NVS wire format: SSID 32+1, MQTT host 64+1, passwords 64+1, user
// 32+1, client id 48+1. Real credentials are never constants in source.
constexpr size_t WIFI_SSID_CAP = 33;
constexpr size_t WIFI_PASS_CAP = 65;
constexpr size_t MQTT_HOST_CAP = 65;
constexpr size_t MQTT_USER_CAP = 33;
constexpr size_t MQTT_PASS_CAP = 65;
constexpr size_t CLIENT_ID_CAP = 49;

// Firmware release reported in the retained device-info payload (micropad/device)
// so the backend/UI can see which build is on the pad and refuse a config whose
// fields this build would clip. Bump on behaviour changes that affect the wire
// contract's limits.
inline constexpr const char *FIRMWARE_VERSION = "1.2.0";

// Counters the pad keeps about itself: what the panel never shows, but a
// dashboard can. Pure data plus one formatter, so the sketch only bumps counters
// and publishes the retained micropad/diag payload.
struct Diagnostics {
  uint32_t uptimeS = 0;
  uint32_t mqttConnects = 0;
  uint32_t catalogParses = 0;
  uint32_t catalogRejects = 0;
  uint32_t pageRejects = 0;
  uint32_t keymapRejects = 0;
  uint32_t eventDrops = 0;
  uint32_t heapFreeBytes = 0;
  uint32_t heapMinBytes = 0;
  // Loop task stack high-water mark, in bytes still free at the deepest call. This is
  // the measurement the 16 KiB loop stack is sized from: the linked frame sizes say the
  // worst callback uses 6,704 B, and this number confirms it on the real device - which
  // is why it belongs in the payload and not only in a debug build.
  uint32_t stackMinBytes = 0;
};

// Rendered payload budget: fixed key order, integer values only. The worst case (ten
// 10-digit values) measures 254 bytes and is pinned byte-for-byte by the host test; the
// cap leaves room for one more field. The formatter returns 0 rather than a half-written
// object, so a caller can never publish truncated JSON.
constexpr size_t DIAGNOSTICS_JSON_CAP = 320;

// Republish interval for the retained diagnostics while connected. Diagnostics
// ride the retained topic (a late subscriber gets the last snapshot), so this is
// about freshness, not delivery.
constexpr uint32_t DIAGNOSTICS_PUBLISH_MS = 60000;

size_t formatDiagnostics(const Diagnostics &diag, char (&dst)[DIAGNOSTICS_JSON_CAP]);

// Loop-task stack handed to the Arduino core (getArduinoLoopTaskStackSize in the
// sketch). The MQTT callback parses PubSubClient's buffer in place, so the frame
// stays far below this; the value is a core constant so the device-info payload,
// the sketch and the static tests cannot disagree.
constexpr size_t LOOP_TASK_STACK_BYTES = 16384;

enum class KeyId : uint8_t {
  R0C0, R0C1, R0C2, R0C3, R1C0, R1C1, R1C2,
  R1C3, R2C0, R2C1, R2C2, R2C3, EncUp, EncDown
};
enum class Action : uint8_t {
  None, Enter, Back, Home, Settings, Scroll, ScrollUp, ScrollDown,
  Navigate, Keymap, GetAllPages, Toggle, On, Off, Press, VolumeUp,
  VolumeDown, MediaNext, MediaPrev, Edit, Confirm, Adjust
};
enum class DrawReason : uint8_t {
  Boot, StateChange, PortalEnter, PortalExit, PowerEdge, Recovery, PartialLimit
};
enum class ItemType : uint8_t {
  Category, Light, Switch, Script, Button, Scene, Sensor,
  MediaPlayer, Number, Settings, Back, Cover, Fan, InputBoolean, Lock
};
// How one row renders its value column: as text (the Home Assistant state or a
// number), as a checkbox (a two-valued state) or as a slider bar (an item whose
// control is `slider`). Derived from the item, never configured separately on the
// panel side.
enum class RowStyle : uint8_t { Text, Checkbox, Slider };
constexpr size_t ROW_STYLE_COUNT = static_cast<size_t>(RowStyle::Slider) + 1;

// How a row is driven. `Button` is the historical behaviour (a press runs the
// row's action); `Slider` adds an encoder turn that adjusts the row's value while
// the cursor rests on it, without an Enter press. Mirrors the contract's
// `controls` list, which the editor reads.
enum class Control : uint8_t { Button, Slider };
constexpr size_t CONTROL_COUNT = static_cast<size_t>(Control::Slider) + 1;

// LongPress is emitted once per hold, LONG_PRESS_MS after the debounced press.
// DoublePress replaces the second press of a double press when the key has a
// bound double gesture; the first press of that pair is delivered as a deferred
// Pressed unless the second press arrives inside DOUBLE_PRESS_MS.
enum class Edge : uint8_t { None, Pressed, Released, LongPress, DoublePress };
enum class PowerEdge : uint8_t { None, Connected, Disconnected };

// Enum-bounded counts. Derived from the enum instead of typed as literals so a
// new action/item type cannot leave a table short or a loop bound stale; the
// static tests additionally assert these track contracts/mqtt-contract.json.
constexpr size_t ACTION_COUNT = static_cast<size_t>(Action::Adjust) + 1;
// Off the *last* enumerator: appending an item type must update this line, and
// tests/integration/test_mqtt_contract.py fails if it stops matching the contract.
constexpr size_t ITEM_TYPE_COUNT = static_cast<size_t>(ItemType::Lock) + 1;

// One row per item type: what a press does by default and whether the type
// accepts the edit/confirm gesture. This is the single place in the firmware
// that maps a type to behaviour — actionForSelectedItem() reads the table, so a
// new item type is one row here plus one entry in the contract's
// item_type_meta (the configurator reads that, and a static test asserts the two
// agree on defaultAction/editable).
struct ItemTypeDescriptor {
  ItemType type;
  Action defaultAction;
  // What a *hold* on the row does (a tap keeps defaultAction). None means the
  // hold is ignored for this type, which is the default for anything without a
  // meaningful second action.
  Action alternateAction;
  bool editable;  // Confirm replaces defaultAction while the pad edits this row
  // True when the type carries a value an encoder turn can move (and therefore
  // may be configured as a slider row). Mirrors the contract's `adjustable` flag.
  bool adjustable;
};
extern const ItemTypeDescriptor ITEM_TYPE_DESCRIPTORS[ITEM_TYPE_COUNT];
const ItemTypeDescriptor &itemTypeDescriptor(ItemType type);

// One action plus its optional context strings (see "Fixed Data Model"). entity
// is the target HA entity id, targetPage the destination page. Used for a key's
// tap and for each of its gestures.
struct BindingTarget {
  Action action = Action::None;
  char entity[ENTITY_CAP] = {};
  char targetPage[PAGE_ID_CAP] = {};
};

// One key's effective binding: the tap target plus the two gesture targets. An
// unbound gesture (action None) never fires and leaves the key's behaviour
// exactly as it was before gestures existed, which is what keeps an older
// published keymap valid.
struct Binding {
  Action action = Action::None;
  char entity[ENTITY_CAP] = {};
  char targetPage[PAGE_ID_CAP] = {};
  BindingTarget hold;
  BindingTarget doubleTap;
};

// A decoded, debounced hardware event. direction is meaningful for encoder
// keys: -1 up/counterclockwise, +1 down/clockwise, 0 otherwise.
struct InputEvent {
  KeyId key;
  bool pressed;
  int8_t direction;
  uint32_t atMs;
  // True for the synthetic hold event emitted LONG_PRESS_MS into a press (see
  // DebouncedInput). Actions resolve the same way; only the caller decides that a
  // hold means "the other thing". Declared last so the existing four-field
  // aggregate initializers (and any future one) stay valid and default to a tap.
  bool longPress = false;
  // True for the second press of a double press (see DebouncedInput). Declared
  // last for the same reason as longPress.
  bool doublePress = false;
};

// Per-key active-low debouncer (see "Debounced Matrix and Encoder Input").
// update() consumes one raw sample at wall-clock nowMs and returns an edge only
// when the same candidate has been stable for INPUT_DEBOUNCE_MS. No delays.
// primeForWake() re-initializes the debounce baseline at wake: it collapses
// the candidate and stable state to the wake-time raw level so a level that is
// already true at wake (the press that woke the device) is never re-emitted by
// the first ordinary scans — the sketch's immediate wake scan already
// delivered the press — while a return to the other level still produces a
// Released edge, and a differing raw level debounces from nowMs rather than
// being accepted silently as a new baseline.
class DebouncedInput {
 public:
  // Returns None when nothing changed, the debounced edge on a change, and
  // LongPress exactly once while a debounced press is held past LONG_PRESS_MS.
  // With the double-press filter enabled the press edge is deferred by up to
  // DOUBLE_PRESS_MS: a second press inside that window reports DoublePress (and
  // the deferred Pressed is dropped), otherwise the deferred Pressed is returned
  // as soon as the window closes.
  Edge update(bool rawPressed, uint32_t nowMs);
  void primeForWake(bool rawPressed, uint32_t nowMs);
  bool held() const;
  // Enables the double-press filter for this key. The caller (scanMatrixPass,
  // fed from the effective keymap) enables it only for keys that bind a double
  // gesture, so no other key pays the deferral.
  void setDoublePressEnabled(bool enabled);
  bool doublePressEnabled() const { return doublePressEnabled_; }

 private:
  bool stable_ = false;
  bool candidate_ = false;
  bool longPressFired_ = false;
  uint32_t candidateSinceMs_ = 0;
  uint32_t pressedAtMs_ = 0;
  bool doublePressEnabled_ = false;
  // A debounced press whose tap is waiting for the double-press window, and the
  // release that armed the second press. Both are cleared by primeForWake.
  bool tapPending_ = false;
  bool secondPressArmed_ = false;
  uint32_t tapPendingAtMs_ = 0;
  uint32_t releasedAtMs_ = 0;
};

// 16-state quadrature decoder tuned for the board's two transitions per
// mechanical detent. Impossible two-bit jumps drop partial progress.
class QuadratureDecoder {
 public:
  void reset(uint8_t ab);
  int8_t update(uint8_t ab);

 private:
  uint8_t past_ = 0;
  int8_t accum_ = 0;
};

// Debounce-guarded USB host state (see "Debounce Guarded USB Host Detection").
// sample() consumes one raw CDC-host probe at wall-clock nowMs and returns an
// edge only when the same candidate has remained stable for USB_STABLE_MS: a
// raw change restarts the candidate window, the first sample only seeds the
// baseline, and repeated samples of the accepted state emit nothing. The
// sketch's powerTick() additionally samples no more often than USB_SAMPLE_MS
// and performs its side effects only on the returned edges. Dependency-free
// and hardware-free: the raw bool is isPowered() (data lines only, never a
// VBUS/charger GPIO guess).
class PowerDebouncer {
 public:
  PowerEdge sample(bool rawConnected, uint32_t nowMs);

 private:
  bool stable_ = false;
  bool candidate_ = false;
  uint32_t candidateSinceMs_ = 0;
  bool initialized_ = false;
};

// Warm light-sleep eligibility predicate (see "Sleep and Power Indicator").
// False while USB powered, a key is held, a render is active or pending, less
// than IDLE_SLEEP_MS of idle has elapsed since lastActivityMs, or less than
// MIN_AWAKE_MS has elapsed since awakeStartedMs (the post-wake brake). Every
// elapsed comparison is unsigned wrap-safe.
bool canSleep(bool powered, bool keyHeld, bool renderBusy,
              bool renderPending, uint32_t nowMs, uint32_t lastActivityMs,
              uint32_t awakeStartedMs);

// Fixed page and application data model (see "Fixed Data Model").
struct Item {
  char name[ITEM_NAME_CAP];
  ItemType type;
  char entity[ENTITY_CAP];
  char state[STATE_CAP];
  float value;
  float min;
  float max;
  float step;
  char unit[UNIT_CAP];
  bool editable;
  Control control = Control::Button;
  char targetPage[PAGE_ID_CAP];
};
struct Page {
  char pageId[PAGE_ID_CAP];
  char title[TITLE_CAP];
  char parent[PAGE_ID_CAP];
  uint8_t itemCount;
  Item items[MAX_ITEMS_PER_PAGE];
};
struct Catalog { uint8_t pageCount; Page pages[MAX_PAGES]; };
struct AppState {
  char pageId[PAGE_ID_CAP];
  uint8_t selected;
  uint8_t firstVisible;
  bool editing;
  uint32_t lastInputMs;
  bool resyncPending;
};

// A compact outbound event about to be published. pageId is the page the
// event was triggered on (the backend dispatches on event.page_id); value is
// meaningful only for Edit. Replaceable events (ScrollUp/ScrollDown/Edit) may
// be coalesced or evicted when the queue is full.
struct Event {
  Action action = Action::None;
  char entity[ENTITY_CAP] = {};
  char pageId[PAGE_ID_CAP] = {};
  char targetPage[PAGE_ID_CAP] = {};
  float value = 0.0f;
};

// Outcome of applying one resolved action to the cached application state.
struct ApplyResult {
  bool stateChanged;
  bool eventReady;
};

// Device network settings (WiFi + MQTT). Dependency-free: the NVS read/write
// lives in the Arduino sketch; here only the neutral defaults and the
// validation rule, which host tests can exercise without NVS.
struct Settings {
  char wifiSsid[WIFI_SSID_CAP];
  char wifiPassword[WIFI_PASS_CAP];
  char mqttHost[MQTT_HOST_CAP];
  uint16_t mqttPort;
  char mqttUser[MQTT_USER_CAP];
  char mqttPassword[MQTT_PASS_CAP];
  char clientId[CLIENT_ID_CAP];
};

// One of the four visible rows carried by a render snapshot. Copied strings
// and scalars only: no pointers into the catalog, JSON document, network
// objects, or application state.
struct RenderRow {
  char name[ITEM_NAME_CAP];
  char state[STATE_CAP];
  char unit[UNIT_CAP];
  float value;
  // Range of a slider row, used only to size the bar's fill.
  float min;
  float max;
  bool selected;
  bool editing;
  // How the value column is drawn, derived from the item: Slider for a slider
  // row (the bar plus the numeric value), Checkbox for a two-valued state (the
  // box instead of the word "on"/"off"), Text otherwise.
  RowStyle style = RowStyle::Text;
};

// A self-contained, immutable description of everything the display needs.
// Core 1 fills one slot through SnapshotMailbox while Core 0 draws the newest
// published slot; publication never mutates the snapshot bytes.
struct RenderSnapshot {
  uint32_t generation;
  bool portal;
  bool forceFull;
  bool usbHost;
  uint8_t networkState;
  uint8_t rowCount;
  uint8_t totalItems;
  uint8_t firstVisible;
  char title[TITLE_CAP];
  char portalSsid[33];
  char portalPassword[SETUP_PASSWORD_LENGTH + 1];
  char portalAddress[16];
  RenderRow rows[VISIBLE_ROWS];
};

// Two-slot snapshot mailbox. Exactly one slot may be Rendering at a time;
// while it renders, Core 1 may only write (beginWrite..publish) the other
// slot, so the renderer always consumes an immutable snapshot and repeated
// requests during a refresh coalesce into the newest generation. This class
// is dependency-free: its methods only mutate slot metadata and snapshot
// bytes; the FreeRTOS critical-section wrapping lives in the sketch.
class SnapshotMailbox {
 public:
  static constexpr int8_t SLOT_COUNT = 2;

  // Claim the non-rendering slot and mark it Writing, or -1 when both slots
  // are busy. The caller fills it outside any critical section, then calls
  // publish().
  int8_t beginWrite();

  // Reference to the Writing slot's snapshot bytes (fill, then publish()).
  RenderSnapshot &writable(int8_t slot);

  // Mark the Writing slot Published, demote any older published slot to Free,
  // record the newest slot index, and advance the generation counter. Never
  // touches the snapshot bytes.
  void publish(int8_t slot);

  // Atomically claim the newest Published slot for rendering and mark it
  // Rendering, or -1 when nothing is published.
  int8_t claimNewest();

  const RenderSnapshot &readable(int8_t slot) const;

  // Mark a rendered slot Free again.
  void release(int8_t slot);

  // True while any slot is Published or Writing (a render is pending or in
  // flight); Rendering alone never counts.
  bool hasPending() const;

  // Generation counter: how many snapshots have been published so far. The
  // snapshot currently being built is currentGeneration() + 1.
  uint32_t currentGeneration() const { return generation_; }

 private:
  // The host test binary verifies that publication never mutates snapshot
  // bytes by comparing the slot memory directly.
  friend struct SnapshotMailboxHostTestAccess;
  enum class SlotState : uint8_t { Free, Writing, Published, Rendering };
  struct Slot {
    SlotState state = SlotState::Free;
    RenderSnapshot snapshot = {};
  };
  int8_t renderedSlot() const;

  Slot slots_[SLOT_COUNT];
  int8_t newestIndex_ = -1;
  uint32_t generation_ = 0;
};

// ============================================================================
// Task 9: landscape UI geometry and refresh policy. The logical canvas is
// 296x128 landscape; the native SSD1680 panel is 128x296 and the sketch maps
// the rotated user space (setRotation(1)) onto the full native window. The
// geometry builders are dependency-free: they emit a deterministic prim model
// (lines, rects, circles, triangles, text spans) that the sketch translates
// into GxEPD2 draw calls inside drawSnapshot(), so every layout rule is
// host-testable without hardware.
// ============================================================================

// --- Logical canvas and fixed regions (296x128 landscape) ---
constexpr int16_t CANVAS_WIDTH = 296;
constexpr int16_t CANVAS_HEIGHT = 128;
constexpr int16_t TITLE_STRIP_Y = 0;          // full-width title/status strip
constexpr int16_t TITLE_STRIP_HEIGHT = 24;    // y 0..23
constexpr int16_t STRIP_SEPARATOR_Y = 23;
constexpr int16_t ROW_AREA_Y = 24;            // first item row top
constexpr int16_t ROW_HEIGHT = 26;            // four equal rows: 24..49 .. 102..127
constexpr int16_t STATUS_CELL_X = 276;        // network status cell x 276..295
constexpr int16_t STATUS_CELL_W = 20;
constexpr int16_t STATUS_CELL_Y = 2;
constexpr int16_t STATUS_CELL_H = 20;
constexpr int16_t POWER_SLOT_X = 256;         // power slot x 256..275, left of cell
constexpr int16_t POWER_SLOT_W = 20;
constexpr int16_t SCROLLBAR_X = 291;          // scrollbar gutter inside y 24..127
constexpr int16_t SCROLLBAR_W = 2;
constexpr int16_t SCROLLBAR_AREA_TOP = ROW_AREA_Y;
constexpr int16_t SCROLLBAR_AREA_HEIGHT = 104;  // 24..127 inclusive
constexpr int16_t TITLE_TEXT_X = 4;
constexpr int16_t TITLE_CLIP_X = 252;         // clip title before the power slot
constexpr int16_t ROW_TEXT_X = 10;            // after the selection-cursor gutter
constexpr int16_t ROW_NAME_CLIP_X = 150;
constexpr int16_t ROW_VALUE_RIGHT_X = 288;    // right-aligned value, before gutter
constexpr int16_t MONO_CHAR_W = 11;           // FreeMonoBold9pt7b xAdvance (clip math)
constexpr size_t RENDER_TEXT_CAP = 32;
// Slider rows draw a track along the bottom of the row plus a proportional fill;
// checkbox rows draw a box (and two tick lines when it is on) in the value
// column instead of a word. Both are composed from the existing rect/line prims,
// so the panel translator and the browser preview need no new primitive kind.
constexpr int16_t SLIDER_TRACK_X = ROW_TEXT_X;
constexpr int16_t SLIDER_TRACK_W = ROW_VALUE_RIGHT_X - ROW_TEXT_X;
constexpr int16_t SLIDER_TRACK_H = 4;
constexpr int16_t SLIDER_TRACK_BOTTOM_PAD = 3;  // track sits this far above row bottom
constexpr int16_t CHECKBOX_SIZE = 14;
constexpr int16_t CHECKBOX_RIGHT_X = ROW_VALUE_RIGHT_X;
// Measured worst cases (host probe, testRenderPrimBudgetHeadroom): a full 4-row
// page with selection cursor, title, network cell, scrollbar and power symbol
// emits 22 prims with text rows, and 30 with four slider rows (track, fill and
// value each) or four ticked checkbox rows. 40 keeps the documented 25 % headroom
// over the worst case instead of the one prim 32 would have left. Each prim is
// 50 bytes of render-task stack (8 KiB total), so the extra 8 prims cost 400 B.
constexpr size_t RENDER_PRIM_CAP = 40;
constexpr uint32_t DISPLAY_BUSY_TIMEOUT_MS = 15000;

// Network state values a snapshot carries (mirrors the sketch enum): 1
// disconnected, 2 connecting, 3 Wi-Fi only, 4 MQTT connected.
constexpr uint8_t NETWORK_STATE_DISCONNECTED = 1;
constexpr uint8_t NETWORK_STATE_CONNECTING = 2;
constexpr uint8_t NETWORK_STATE_WIFI_ONLY = 3;
constexpr uint8_t NETWORK_STATE_MQTT = 4;

// Wrap-safe elapsed milliseconds: the same unsigned wrap behaviour every
// millis()-based interval in the sketch already relies on (2^32 ms rollover).
constexpr uint32_t elapsedMs(uint32_t nowMs, uint32_t thenMs) {
  return static_cast<uint32_t>(nowMs - thenMs);
}

// Full refresh is forced for boot, portal transitions, BUSY recovery, and the
// periodic partial-limit refresh that limits ghosting.
constexpr bool refreshMustBeFull(DrawReason reason) {
  return reason == DrawReason::Boot || reason == DrawReason::PortalEnter ||
         reason == DrawReason::PortalExit || reason == DrawReason::Recovery ||
         reason == DrawReason::PartialLimit;
}

enum class RefreshMode : uint8_t { Full, Partial };

// Sleep/render handshake (P1.2): the sleep path and the render task serialize
// through this interlock inside the sketch's critical section, so a draw can
// never begin after sleep was granted and sleep can never start while a draw
// is in flight or a snapshot is pending. The state transitions are
// dependency-free and host-testable for every interleaving; the sketch wraps
// each transition (plus the renderBusy and mailbox-pending reads) in the same
// FreeRTOS critical section to make them atomic across the two cores.
class SleepInterlock {
 public:
  // Render task: claim permission to draw. False while sleep is entered, so
  // no new draw starts after the interlock was granted until wake().
  bool tryBeginDraw() const { return state_ == State::Awake; }

  // Sleep path: grant sleep only when no draw is in flight and no snapshot is
  // pending. The caller must hold the critical section while supplying the
  // freshly read flags; on success every later tryBeginDraw() returns false.
  bool setSleepEntered(bool renderBusy, bool snapshotPending) {
    if (state_ != State::Awake) return false;
    if (renderBusy || snapshotPending) return false;
    state_ = State::SleepEntered;
    return true;
  }

  // Wake: draws may start again. Returns true only when sleep was entered.
  bool wake() {
    if (state_ != State::SleepEntered) return false;
    state_ = State::Awake;
    return true;
  }

  bool sleeping() const { return state_ == State::SleepEntered; }

 private:
  enum class State : uint8_t { Awake, SleepEntered };
  State state_ = State::Awake;
};

// Refresh policy of the Core 0 render task: decides full vs partial per
// refresh, keeps the partial counter (reset only by a successful full
// refresh, incremented only after a successful partial refresh), and enforces
// the wrap-safe MIN_REFRESH_SPACING_MS interval between refresh starts.
class RefreshPolicy {
 public:
  // True when at least MIN_REFRESH_SPACING_MS elapsed since the previous
  // refresh start (wrap-safe; also true after a 2^32 ms rollover).
  bool spacingElapsed(uint32_t nowMs) const {
    return elapsedMs(nowMs, lastStartMs_) >= MIN_REFRESH_SPACING_MS;
  }
  // Remaining wait so the caller may block only the missing interval.
  uint32_t remainingSpacingMs(uint32_t nowMs) const {
    const uint32_t elapsed = elapsedMs(nowMs, lastStartMs_);
    return elapsed < MIN_REFRESH_SPACING_MS ? MIN_REFRESH_SPACING_MS - elapsed
                                            : 0;
  }
  // Decide the next refresh mode and record the refresh start. Full is forced
  // when the snapshot demands it, after a BUSY recovery, or when
  // MAX_PARTIAL_REFRESHES partial refreshes have run (anti-ghosting).
  RefreshMode beginRefresh(uint32_t nowMs, bool snapshotForceFull) {
    lastStartMs_ = nowMs;
    if (snapshotForceFull || recoveryFull_ ||
        (MAX_PARTIAL_REFRESHES > 0 &&
         partialCount_ >= MAX_PARTIAL_REFRESHES)) {
      lastMode_ = RefreshMode::Full;
      return RefreshMode::Full;
    }
    lastMode_ = RefreshMode::Partial;
    return RefreshMode::Partial;
  }
  // Call after the panel finished: a failed (BUSY timeout) refresh enters
  // recovery so the next refresh is forced full; a successful full refresh
  // resets the partial counter; a successful partial increments it.
  void completeRefresh(bool success) {
    if (!success) {
      recoveryFull_ = true;
      return;
    }
    if (lastMode_ == RefreshMode::Full) {
      partialCount_ = 0;
      recoveryFull_ = false;
    } else if (partialCount_ < MAX_PARTIAL_REFRESHES) {
      ++partialCount_;
    }
  }
  void requireRecoveryFull() { recoveryFull_ = true; }
  uint8_t partialCount() const { return partialCount_; }
  bool recoveryPending() const { return recoveryFull_; }
  RefreshMode lastMode() const { return lastMode_; }
  uint32_t lastStartMs() const { return lastStartMs_; }

 private:
  uint32_t lastStartMs_ = 0;
  uint8_t partialCount_ = 0;
  bool recoveryFull_ = false;
  RefreshMode lastMode_ = RefreshMode::Full;
};

// --- Deterministic prim model (text-representable, host-testable) ---
enum class PrimKind : uint8_t {
  Line, Rect, FillRect, Circle, FillCircle, FillTriangle, Text
};
enum class TextAlign : uint8_t { Left, Right };
constexpr uint8_t PRIM_COLOR_BLACK = 0;
constexpr uint8_t PRIM_COLOR_WHITE = 1;

// One drawable. Lines carry x0/y0..x1/y1; rects x0/y0 + w/h; circles the
// center + radius; triangles v1=(x0,y0), v2=(x1,y1), v3=(w,h); text spans the
// box (x0: left, y0: top, h: height) plus alignment anchor.
struct RenderPrim {
  PrimKind kind = PrimKind::Line;
  int16_t x0 = 0;
  int16_t y0 = 0;
  int16_t x1 = 0;
  int16_t y1 = 0;
  int16_t w = 0;
  int16_t h = 0;
  uint8_t radius = 0;
  uint8_t color = PRIM_COLOR_BLACK;
  uint8_t align = 0;  // TextAlign
  char text[RENDER_TEXT_CAP] = {};
};

struct RenderModel {
  RenderPrim prims[RENDER_PRIM_CAP];
  uint16_t count = 0;
};

void addLine(RenderModel &model, int16_t x0, int16_t y0, int16_t x1,
             int16_t y1);
void addRect(RenderModel &model, int16_t x, int16_t y, int16_t w, int16_t h,
             uint8_t color);
void addFillRect(RenderModel &model, int16_t x, int16_t y, int16_t w,
                 int16_t h, uint8_t color);
void addCircle(RenderModel &model, int16_t cx, int16_t cy, uint8_t radius,
               uint8_t color);
void addFillCircle(RenderModel &model, int16_t cx, int16_t cy, uint8_t radius,
                   uint8_t color);
void addFillTriangle(RenderModel &model, int16_t x0, int16_t y0, int16_t x1,
                     int16_t y1, int16_t x2, int16_t y2);
void addText(RenderModel &model, const char *text, int16_t x, int16_t y,
             int16_t boxHeight, TextAlign align);

// Copy src truncated to maxChars and to the buffer capacity; returns the
// number of characters copied.
size_t clipText(char (&dst)[RENDER_TEXT_CAP], const char *src,
                size_t maxChars);

// Copy src truncated to maxChars, appending a trailing ellipsis ("...")
// whenever the source did not fit, so a clipped row name reads as truncated
// instead of ending mid-word. The ellipsis consumes up to three of the
// maxChars budget; a source that fits is copied unchanged.
size_t clipTextEllipsis(char (&dst)[RENDER_TEXT_CAP], const char *src,
                        size_t maxChars);

// Right-aligned value span for one item row: integral values print without a
// decimal, others with one decimal, then the unit; editing wraps the span in
// angle brackets without changing the row layout.
void formatRowValue(char (&dst)[RENDER_TEXT_CAP], const RenderRow &row);

// True for a two-valued state that the panel draws as a checkbox instead of the
// literal word: "on"/"off" and "true"/"false", case-insensitively. Everything
// else (a sensor reading, a media state, an empty state) keeps the text column,
// so the checkbox never invents a boolean where the entity has none.
bool booleanState(const char *state);
// True when the state is the "on" (or "true") side of that pair.
bool booleanStateOn(const char *state);
// Pixel width of a slider row's bar fill, derived from the row's [min, max] and
// value and clamped to the track's inner width. 0 for an empty range or a value
// at the bottom of it, the full inner width at the top.
int16_t sliderFillWidth(const RenderRow &row);

// Full landscape UI model for the non-portal snapshot: title/status strip,
// four fixed item rows with selection, and the conditional scrollbar.
void layoutNormalUi(const RenderSnapshot &snap, RenderModel &model);

// Fill the page-derived part of a snapshot: title, item count, the visible row
// window (name/state/unit/value) and the selection/edit flags, using the same
// safeCopy clipping the payload parser uses. The sketch adds the fields only it
// knows (generation, portal flag and data, network state, USB host); the host
// simulator calls this directly, so "what the panel shows for this page" has one
// implementation instead of a second one mirrored in test code.
void fillPageSnapshot(const Page &page, const AppState &state, RenderSnapshot &out);

// Portal view: title strip plus SSID / password / address rows.
void layoutPortalUi(const RenderSnapshot &snap, RenderModel &model);

// Network status cell geometry: solid square (MQTT), outline ring (Wi-Fi
// only), two filled dots while connecting/disconnected. Geometry only.
void networkStatusPrims(uint8_t networkState, RenderModel &model);

// USB power symbol geometry (stem, fork, arrow head, square and circular
// terminals) entirely from Line/Rect/Circle prims; never text.
void powerIconPrims(bool usbHost, RenderModel &model);

// The scrollbar appears only when the page holds more than the four visible
// rows.
constexpr bool scrollbarNeeded(uint8_t totalItems) {
  return totalItems > static_cast<uint8_t>(VISIBLE_ROWS);
}

// Thumb geometry bounded to the row-area track (y 24..127).
void scrollbarThumb(uint8_t totalItems, uint8_t firstVisible,
                    int16_t &thumbY, int16_t &thumbH);

// Neutral defaults: placeholder broker/user/pass (never real credentials), an
// empty SSID so a first-boot portal can capture credentials, and the standard
// MQTT port. Every field is bounded and zero-initialized.
Settings defaultSettings();

// Accepts ports 1-65535, a nonempty host and user, and either an empty SSID
// (first-boot portal) or a nonempty SSID with a password of 8-64 characters.
// Client id may be empty.
bool validateSettings(const Settings &settings);

// Why a settings record is rejected, as a short human-readable reason ("" when it
// is valid). validateSettings() is defined in terms of this, so the rule set and
// the message can never drift apart. Never contains a credential.
const char *settingsError(const Settings &settings);

// Fixed-capacity outgoing event queue with no heap allocation. Full-queue
// policy: a replaceable event coalesces with a matching replaceable slot, else
// the oldest replaceable slot is evicted; only when no slot can be coalesced
// or evicted is the push rejected and overflow incremented.
class EventQueue {
 public:
  EventQueue() = default;
  bool push(const Event &event);
  bool pushFront(const Event &event);
  bool pop(Event &out);
  size_t size() const { return size_; }
  size_t overflowCount() const { return overflow_; }

 private:
  static bool isReplaceable(const Event &e);
  static bool matches(const Event &a, const Event &b);
  bool coalesce(const Event &event);
  bool evictOldestReplaceable();

  Event slots_[QUEUE_CAPACITY];
  size_t head_ = 0;
  size_t size_ = 0;
  size_t overflow_ = 0;
};

using EventDispatchFn = void (*)(const Event &event);

// Bounded catalog lookup: pages are searched only through pageCount.
Page *findPage(Catalog &catalog, const char *pageId);
const Page *findPage(const Catalog &catalog, const char *pageId);

// Keep selection inside the item range and inside the four-row visible window.
void normalizeSelection(const Page *page, AppState &state);

// One item-type-driven action for the selected item (see brief Step 6a).
Action actionForSelectedItem(const Item &item, bool editing);

// The type's second action for a hold on that row; Action::None when the
// type has no meaningful alternate (see ITEM_TYPE_DESCRIPTORS).
Action alternateActionForSelectedItem(const Item &item);

// Pop and dispatch up to budget events, returning how many were dispatched.
size_t flushEvents(EventQueue &queue, EventDispatchFn dispatch, size_t budget);

// Predictive application state. The core owns the fixed catalog (filled by the
// MQTT cache subscription in a later task), the effective keymap, and the
// outgoing event queue. applyAction() mutates cache-backed UI state
// optimistically and enqueues compact events; it never publishes directly.
// Every supported action reaches a defined outcome: a local state update
// and/or a documented MQTT event, or an explicit no-op (None, a raw
// non-directional Scroll, Enter on sensor/media items, and the reserved
// get_all_pages, which only the sketch's first MQTT connection may queue).
struct PadController {
  Catalog catalog = {};
  AppState state = {};
  Binding activeKeymap[KEY_COUNT] = {};
  EventQueue queue = {};

  Page *currentPage();
  Item *currentItem();
  // direction carries the encoder turn (-1 up/counterclockwise, +1
  // down/clockwise, 0 for a matrix key) so Adjust can step the selected row's
  // value; every other action ignores it.
  ApplyResult applyAction(Action action, const Binding &binding,
                          uint32_t atMs, int8_t direction = 0);
  Action selectedItemAction() const;
  Action selectedItemLongPressAction() const;
  bool resyncDue(uint32_t nowMs) const;
  void noteInputBurst(uint32_t atMs);
};

// Commit a fully validated authoritative page (micropad/page/current) into the
// catalog: replace the matching page in place, or append a new slot when the
// id is unknown. The page is copied whole (never merged). When the committed
// page is the one currently displayed, selection is clamped to the new item
// count, stale edit mode is cleared, and a fulfilled quiet-period resync is
// cleared. On any rejection (empty page id, over-limit item count, full
// catalog) nothing is changed.
bool commitCurrentPage(PadController &pad, const Page &page, uint32_t nowMs);

// Exact comparison of all page fields that affect rendering or actions. This
// suppresses redundant authoritative MQTT echoes after optimistic local draws.
bool pageContentEqual(const Page &a, const Page &b);

// Replace the entire effective keymap with the validated staging array in one
// operation. The whole array is copied; omitted or stale entries are never
// preserved.
void replaceKeymap(Binding (&dst)[KEY_COUNT], const Binding (&src)[KEY_COUNT]);

// Bounded, always-terminating string copy. Returns true only when the whole
// source (minus its terminator) fit in the destination.
template <size_t N>
bool safeCopy(char (&dst)[N], const char *src) {
  static_assert(N > 0, "destination must have capacity");
  if (src == nullptr) {
    dst[0] = '\0';
    return false;
  }
  const size_t length = std::strlen(src);
  const size_t copied = length < N - 1 ? length : N - 1;
  std::memcpy(dst, src, copied);
  dst[copied] = '\0';
  return length < N;
}

// Bounded, truncating copy for display-only strings (title, item name, state,
// unit). Returns true when the whole source fit, false when it was clipped.
// Unlike safeCopy() this never fails: a long Home Assistant state or item name
// must not discard an entire page or catalog payload, because the rejection
// would be invisible on the device (no log, no draw) and the panel would keep
// showing stale content. Identifier fields still use the strict safeCopy().
template <size_t N>
bool safeCopyClip(char (&dst)[N], const char *src) {
  static_assert(N > 0, "destination must have capacity");
  if (src == nullptr) {
    dst[0] = '\0';
    return false;
  }
  const size_t length = std::strlen(src);
  const size_t copied = length < N - 1 ? length : N - 1;
  std::memcpy(dst, src, copied);
  dst[copied] = '\0';
  return length <= N - 1;
}

// Key/action/item-type string conversions (see "Key Name and Action Tables").
const char *keyIdName(KeyId key);
bool parseKeyId(const char *text, KeyId &out);
const char *actionName(Action action);
bool parseAction(const char *text, Action &out);
bool parseItemType(const char *text, ItemType &out);
const char *controlName(Control control);
bool parseControl(const char *text, Control &out);
void loadDefaultKeymap(Binding (&out)[KEY_COUNT]);
Action resolveBinding(const Binding &binding, const InputEvent &input);

// The binding one input actually carries out: a bound gesture replaces the tap
// target (its own action, entity and target page - a hold may address a
// different entity than the tap), an unbound gesture leaves the tap target in
// place. resolveBinding() answers the action, this answers action plus context.
void effectiveBinding(Binding &out, const Binding &binding,
                      const InputEvent &input);

// Host-testable input scanning. The raw GPIO is owned by thin adapters the
// Arduino sketch supplies over real pins; host tests supply their own.
using MatrixReadFn = bool (*)(uint8_t row, uint8_t col);
using EventEmitFn = void (*)(const InputEvent &input);
// Per-key double-press gate: true for a key whose effective binding carries a
// double gesture, which is the only case where a tap is deferred.
using DoubleEnableFn = bool (*)(uint8_t keyIndex);

// Matrix switch index for (row, col): matches the KeyId ordering r0c0..r2c3.
constexpr uint8_t matrixKeyIndex(uint8_t row, uint8_t col) {
  return static_cast<uint8_t>(row * MATRIX_COLS + col);
}

// Pressed event for a completed encoder detent. Clockwise/right (+1) is
// EncDown and selects down/next; counterclockwise/left (-1) is EncUp.
constexpr InputEvent encoderEvent(int8_t step, uint32_t nowMs) {
  return InputEvent{step > 0 ? KeyId::EncDown : KeyId::EncUp, true, step,
                    nowMs};
}

// One active-low matrix pass. readKey owns driving its row LOW and restoring
// it HIGH before returning; emit receives each debounced edge.
void scanMatrixPass(DebouncedInput (&debounce)[MATRIX_KEY_COUNT],
                    MatrixReadFn readKey, EventEmitFn emit, uint32_t nowMs,
                    DoubleEnableFn doubleEnabled = nullptr);

// One encoder pass. phase is the raw (A << 1) | B sample; a completed detent
// delivers exactly one pressed event through emit.
void scanEncoderPass(QuadratureDecoder &decoder, uint8_t phase,
                     EventEmitFn emit, uint32_t nowMs);

// True while any debounced matrix switch is held, or a fresh active-low pass
// finds a pressed switch. Resting encoder A/B levels are never a held key.
bool anyMatrixKeyHeld(const DebouncedInput (&debounce)[MATRIX_KEY_COUNT],
                      MatrixReadFn readKey);

}  // namespace micropad
