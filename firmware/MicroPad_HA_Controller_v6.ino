// =============================================================================
// MicroPad Home Assistant Controller  v6 (Light Sleep, power-optimised)
// v6 firmware: async render on core 0, input never blocked by the e-paper
// refresh; portal is escapable; WDT disabled during sleep.
// - No USB CDC conflicts: light sleep only.
// - WiFi/MQTT session is kept across light sleep (modem sleep): wake is
//   instant instead of re-associating, re-DHCPing and redoing the MQTT
//   handshake.
// - Display keeps its image without refresh.
// - Wakes on keys/encoder only; timer wake disabled to save power.
//
// THIS SOFTWARE WAS DEVELOPED WITH AI ASSISTANCE (LLM-generated/co-edited
// code, reviewed by the author). No warranty of any kind; use at your own
// risk. Verify behaviour on your own hardware before relying on it.
//
// ---------------------------------------------------------------------------
// CREDENTIALS:
// Replace mqttUser/mqttPass/mqttServer below with your own values, or enter
// them through the WiFiManager portal on first boot (the portal lets you set
// the MQTT broker; edit the two lines below for user/password). NEVER commit
// real credentials to a public repository.
// ---------------------------------------------------------------------------
//
// -----------------------------------------------------------------------------
// CHANGELOG vs. the previous v6 drop (code review fixes)
// -----------------------------------------------------------------------------
// [FIX-1] Replaced every unsafe strncpy(dst, src, sizeof(dst)) call with a
//         safeCopy() helper that always null-terminates. strncpy() does NOT
//         guarantee a terminating '\0' when the source is >= the destination
//         size - a long "name"/"page_id"/etc. coming from Home Assistant
//         could previously leave a fixed char[] buffer unterminated, and any
//         later strcmp/strlen/canvas.print on it would read past the buffer
//         (garbage on screen at best, a hard fault at worst).
// [FIX-2] Fixed a data race in the render hand-off: renderTaskLoop() used to
//         set `renderBusy = true` BEFORE updating `renderDrawIdx`. In that
//         window requestDraw() (running on the other core) could compute the
//         "safe to write" buffer index from the STALE renderDrawIdx, i.e. it
//         could pick the buffer the render task was about to start reading
//         from a moment later -> a rare, hard-to-reproduce torn frame. The
//         two assignments are now made in the safe order.
// [FIX-3] Removed the dead F-3 partial-refresh bookkeeping. drawPage() has
//         always drawn a full frame unconditionally (the partial-band path
//         was reverted, per the existing comments), yet requestDraw() still
//         recomputed a full "dirty rows" bitmask - including a 20-item loop
//         with 5 strcmp() calls each - on every single key press / encoder
//         tick, for a result nothing downstream ever used. drawIndicatorStrip()
//         and drawRowBand() were unused dead functions. Both removed; this
//         is a pure CPU/flash saving with no behaviour change (the panel was
//         already always fully redrawn).
// [FIX-4] mqttCallback() used to parse the incoming JSON payload once (idDoc,
//         to read page_id / do the prediction-confirm check) and then call
//         applyPageUpdate(json), which parsed the *same* payload a second
//         time. The page-apply logic is now split into
//         applyPageUpdateDoc(JsonDocument&, bool doDraw) which operates on an
//         already-parsed document; mqttCallback() reuses idDoc directly, so
//         every incoming page update is parsed exactly once.
// [FIX-5] handleAllPagesPayload() re-serialised each page from the boot-time
//         catalog into a 2048-byte stack buffer before caching it, even
//         though the MQTT buffer (mqtt.setBufferSize()) allows pages up to
//         16 KB. A page near/at that size would be silently TRUNCATED when
//         re-serialised into the small buffer and cached as broken JSON.
//         The buffer is now 4096 bytes and a truncation check discards
//         (rather than caches) any page that doesn't fit, instead of
//         silently corrupting the cache.
// [FIX-6] Corrected a misleading comment on mqtt.setSocketTimeout(1):
//         PubSubClient interprets this value in SECONDS, not milliseconds,
//         so the real timeout is ~1000 ms, not "800 ms" as previously noted.
// [FIX-7] Added explicit prototypes for bindingIs()/executeBinding() (both
// take a KeyBinding& parameter). Arduino's ctags-based automatic
// prototype generator inserts its generated declarations near the TOP of the
// file, before the KeyBinding struct is defined further
// down - so it was previously generating an invalid prototype and
// failing to compile with "'KeyBinding' does not name a type".
// -------------------------------------------------------------------------
// CHANGELOG vs. GitHub main (2026-09-11 consolidation)
// -------------------------------------------------------------------------
// [M-1] Re-added the media transport actions that exist on GitHub main
//       (merged via PR #5): volume_up / volume_down / media_next /
//       media_prev in executeBinding(). This v6 drop was based on an
//       earlier tree and had dropped them; Home Assistant's automation
//       dispatches them to media_player.media_play_pause / media_next_track
//       / media_previous_track, volume_up / volume_down.
// [M-2] Re-added pollPowerIndicator() from main (PR #5): a 500 ms poll of
//       the USB-VBUS state. Without it, unplugging the pad would leave the
//       power icon on the e-paper until the next input/MQTT event (the
//       panel keeps its image forever). On every power edge it calls
//       requestDraw() so the icon is cleared/applied by the normal render
//       path.
// [M-3] Restored Jonas' power-indicator rendering (PR #5 / dc95978): the
//       v6 drop had reverted it to an else-if chain drawing the U+26A1 bolt
//       via the FreeMonoBold9pt7b glyph - which does not contain that
//       codepoint (invisible) and was additionally hidden whenever the
//       MQTT/WiFi indicator was active. The icon is now a dedicated
//       white-rect "ring + line" slot at LX=270 that always shows while
//       USB power is present, independent of the MQTT/WiFi indicator state.
// =============================================================================

#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <esp_task_wdt.h>
#include <esp_timer.h>
// USB-host detection (usbHostAttached()): TinyUSB's tud_cdc_n_connected()
// only exists in "USB Mode: USB-OTG (TinyUSB)" builds. With "Hardware CDC
// and JTAG" (ARDUINO_USB_MODE=1) there is no TinyUSB stack, so the include
// is conditional and HWCDCSerial reports the host state instead.
#if defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE
// Hardware USB-Serial-JTAG build: no TinyUSB here, HWCDCSerial detects the host.
#else
// TinyUSB build: tud_cdc_n_connected() detects the host.
#include "tusb.h"
#endif
#include "USB.h"
// GxEPD2 prints a diagnostic line ("_Update_Part : 449998") on EVERY panel
// refresh when init() is given a non-zero diag bitrate. On this board Serial
// is USB-CDC: with no host reading the port the TX buffer fills up and
// Serial.print BLOCKS - inside _waitWhileBusy(), i.e. the render task hangs
// forever mid-refresh and the screen freezes. Compiling the diagnostic code
// out entirely (we also call display.init(0)) is what makes navigation
// reliable.
#define DISABLE_DIAGNOSTIC_OUTPUT
#include <GxEPD2_BW.h>
#include <Fonts/FreeMonoBold12pt7b.h>
#include <Fonts/FreeMonoBold9pt7b.h>

// -----------------------------------------------------------------------------
// Debug serial
// -----------------------------------------------------------------------------
// USB-CDC serial output BLOCKS the main loop once the TX buffer is full and
// no host is reading the port. Every "event: {...}" print per button press
// would therefore freeze the UI for ages when the pad is used without a
// serial monitor attached - the exact "extrem laggy" symptom. Keep only
// rare, low-volume messages on the wire; everything per-action goes behind
// MICROPAD_DEBUG and is compiled out for normal use.
#define MICROPAD_DEBUG 0

#if MICROPAD_DEBUG
#define DBG(...) Serial.print(__VA_ARGS__)
#else
#define DBG(...)
#endif

// -----------------------------------------------------------------------------
// String helpers
// -----------------------------------------------------------------------------
// [FIX-1] strncpy(dst, src, sizeof(dst)) does NOT guarantee a terminating
// '\0' when strlen(src) >= sizeof(dst) - the byte at dst[sizeof(dst)-1]
// stays whatever src put there. Every fixed-size char[] field in this
// firmware (page id/title/parent, item name/type/entity/state/value,
// keymap action/entity/target, ...) is filled from server-controlled JSON,
// so an oversized field from Home Assistant could previously leave a
// buffer unterminated - and any later strcmp/strlen/canvas.print on it
// would then read past the end of the buffer. safeCopy() always leaves the
// destination null-terminated, truncating the source if necessary.
static void safeCopy(char* dst, size_t dstSize, const char* src) {
  if (dstSize == 0) return;
  if (!src) { dst[0] = '\0'; return; }
  strncpy(dst, src, dstSize - 1);
  dst[dstSize - 1] = '\0';
}

// -----------------------------------------------------------------------------
// Pin definitions
// -----------------------------------------------------------------------------
#define EPD_CS    8
#define EPD_DC    40
#define EPD_RST   41
#define EPD_BUSY  42

const int ROWS[3] = {47, 48, 45};
const int COLS[4] = {21, 14, 13, 12};
const int ENC_A   = 10;
const int ENC_B   = 11;

// Button mapping (matrix coordinates)
#define BTN_ENTER_R  1
#define BTN_ENTER_C  3
#define BTN_ENTER2_R 0
#define BTN_ENTER2_C 3
#define BTN_BACK_R   2
#define BTN_BACK_C   3
#define BTN_HOME_R   0
#define BTN_HOME_C   0
#define BTN_SETTINGS_R 2
#define BTN_SETTINGS_C 2

// -----------------------------------------------------------------------------
// Display
// -----------------------------------------------------------------------------
GxEPD2_BW<GxEPD2_290_T94_V2, GxEPD2_290_T94_V2::HEIGHT> display(GxEPD2_290_T94_V2(EPD_CS, EPD_DC, EPD_RST, EPD_BUSY));
GFXcanvas1 canvas(304, 48);

// -----------------------------------------------------------------------------
// State
// -----------------------------------------------------------------------------
Preferences prefs;
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);
WebServer server(80);

enum AppState { ST_BOOT, ST_WIFI_PORTAL, ST_LIST };
AppState appState = ST_BOOT;
bool mqttConnected = false;
bool wifiStarted = false;

unsigned long lastInputMs = 0;
unsigned long lastDrawMs = 0;
unsigned long pageRequestSentMs = 0;
String lastDisplayedRaw = "";

// Coalescing flag: set by the main loop, consumed by the render task. Setting
// it while a refresh is already in flight simply schedules one more pass with
// the newest snapshot, so rapid input never queues up a backlog of refreshes.
bool loadingPage = false;

struct MenuItem {
  char name[32];
  char type[16];
  char entity[64];
  // Sensor readings with a unit ("23.5 °C", "unavailable", "1234.5 kWh") do
  // not fit in a 15-char field - the old size silently cut them off.
  char state[24];
  char value[24];
  char target_page[32];
  float minVal;
  float maxVal;
  float step;
  bool editable;
};

struct MenuPage {
  char id[32];
  char title[32];
  char parent[32];
  MenuItem items[20];
  int itemCount = 0;
  int selected = 0;
  int scrollOffset = 0;
} currentPage;

// -----------------------------------------------------------------------------
// Render snapshot + task
// -----------------------------------------------------------------------------
// The e-paper refresh blocks for ~450 ms (SPI + panel waveform). Running it
// inline meant the matrix scan stopped for that whole time: presses landed
// nowhere, so actions could not be spammed and taps got swallowed.
//
// Instead the main loop (core 1) only fills a snapshot and publishes it, and
// a dedicated task on core 0 does the slow refresh. Two snapshot buffers are
// used so the main loop never writes the buffer that is currently on the
// panel: it always writes "the other one" and then flips the published
// index, which makes the hand-off race-free without any locking.
struct RenderSnapshot {
  MenuPage page;
  bool loading;
  bool indM, indW, indT, indP;
  bool inEdit;
  int  editIdx;
  float editVal;
  bool portal;
  // Historic F-3 partial-refresh field. The partial-band refresh path was
  // reverted (see drawPage()/renderTaskLoop() below - the panel is always
  // fully redrawn), so this is no longer read by anything; kept at 0 for
  // struct-layout stability only.
  uint16_t dirty_rows;
};

RenderSnapshot renderBuf[2];
volatile int renderReadIdx = 0;    // last complete snapshot (read by the task)
volatile int renderDrawIdx = -1;   // buffer the task is currently drawing (-1 = idle)
volatile bool renderRequested = false;
volatile bool renderBusy = false;
TaskHandle_t renderTaskHandle = NULL;
bool showPortalScreen = false;
unsigned long portalLastActivityMs = 0;
const unsigned long PORTAL_TIMEOUT_MS = 300000; // auto-reboot after 5 min of portal inactivity

// Wake-loop brake: after waking, stay awake at least this long before the
// pad is allowed to sleep again. Prevents a bounce (floating pin / bouncy
// key / USB activity) from ping-ponging between sleep and wake, which made
// the device look dead.
const unsigned long MIN_AWAKE_MS = 3000;
unsigned long minAwakeUntilMs = 0;

// MQTT reconnect throttle: a failed connect costs up to the socket timeout on
// the main loop, so never retry faster than this. Without it, an unreachable
// broker turns the loop into "block 1 s, run 2 ms, block 1 s, ..." and the
// pad becomes completely unresponsive.
const unsigned long MQTT_RETRY_MS = 5000;
unsigned long nextMqttAttemptMs = 0;
unsigned long nextWifiAttemptMs = 0;

void drawPage(const RenderSnapshot& R);


bool inEditMode = false;
float editValue = 0;
int editItemIndex = -1;

// -----------------------------------------------------------------------------
// Local page cache
// -----------------------------------------------------------------------------
// Raw JSON per page_id, as last confirmed by the server (either from the
// boot-time "get_all_pages" fetch, or from a live single-page reply). This
// is what lets navigation show a page instantly instead of a blank
// "Loading..." screen, while a fresh copy is still requested in the
// background and swapped in if it turns out to differ.
#define MAX_CACHED_PAGES 24
struct CachedPage {
  String id;
  String json;
};
CachedPage pageCache[MAX_CACHED_PAGES];
int pageCacheCount = 0;
bool didFetchAllPages = false;   // only fetch the full catalog once per boot

bool sw[3][4] = {};
bool swPrev[3][4] = {};
uint8_t dc[3][4] = {};
volatile int keyEvents = 0;

volatile uint8_t encPrev = 0;
volatile int encoderTicks = 0;
int lastEncoderTicks = 0;
// Inverted: clockwise/right/up produces +1, counter-clockwise/left/down produces -1
static const int8_t ENC_TABLE[16] = {0,0,1,0, 0,0,0,-1, -1,0,0,0, 0,1,0,0};

String foundNetworks[20];
int foundNetworkCount = 0;

char mqttServer[64] = "192.168.0.100";   // EDIT: your MQTT broker address (192.168.0.100 = placeholder, do not commit real IPs)
char mqttUser[32]   = "micropad";      // EDIT: your MQTT username
char mqttPass[64]   = "replace-me";    // EDIT: your MQTT password (do not commit real credentials)
char wifiSSID[64]   = "";
char wifiPass[64]   = "";

// Small FIFO instead of a single pending slot: boot now fires two events
// back to back ("home" then "get_all_pages") and a single-slot pending
// buffer would silently drop the first one before flushPending() got to it.
// Spamming a toggle fires one event per press. With only 4 slots the queue
// overflowed and dropped the OLDEST events, so Home Assistant executed fewer
// toggles than the pad had already predicted on screen - the displayed state
// then no longer matched reality. Keep enough headroom for a spam burst.
#define PEND_QUEUE_SIZE 16
struct PendingEvent {
  char action[16];
  char entity[64];
  bool hasValue;
  float value;
  char target[32];
};
PendingEvent pendQueue[PEND_QUEUE_SIZE];
int pendHead = 0;
int pendTail = 0;
int pendCount = 0;

// -----------------------------------------------------------------------------
// Configurable key map
// -----------------------------------------------------------------------------
// No key is hard-wired any more. Home Assistant owns the mapping and sends it
// as JSON on "micropad/keymap" (normally retained, so it arrives right after
// the pad subscribes). The defaults below reproduce the classic layout, so a
// pad with an older automation - or none at all - still works.
//
// Bindable "keys":
//   r0c0 .. r2c3   the 12 matrix keys, row*4+col   (r0c3 is the encoder press)
//   enc_up          encoder turned clockwise
//   enc_down        encoder turned counter-clockwise
//
// Each entry: {"action": "...", "entity": "...", "target_page": "..."}
//   none         do nothing
//   enter        activate the selected item
//   back         up one level
//   home         jump to the home page
//   settings     open the WiFi setup portal
//   scroll       (encoder only) move the selection / change the edit value
//   scroll_up    move the selection up one item
//   scroll_down  move the selection down one item
//   navigate     open target_page
//   toggle       toggle entity
//   on / off     turn entity on / off
//   press        run entity (script, button, scene)
//   volume_up / volume_down  media volume (entity required)
//   media_next / media_prev  next / previous track (entity required)
#define KEY_MATRIX_COUNT 12
#define KEY_ENC_UP       12
#define KEY_ENC_DOWN     13
#define KEY_COUNT        14

struct KeyBinding {
  char action[14];
  char entity[64];
  char target[32];
};
KeyBinding keymap[KEY_COUNT];

// [FIX-7] Explicit prototypes for the two functions that take a KeyBinding&
// parameter. Arduino's automatic prototype generator inserts its generated
// declarations near the TOP of the file - before the KeyBinding struct
// above is defined - so letting it auto-generate these two would fail with
// "KeyBinding does not name a type" (exactly the compiler error reported).
// Writing them out here ourselves makes Arduino skip auto-generating a
// (broken) duplicate; drawPage()/RenderSnapshot above already uses the same
// pattern for the same reason.
bool bindingIs(const KeyBinding& b, const char* action);
void executeBinding(const KeyBinding& b, int step);

void setBinding(int idx, const char* action, const char* entity = NULL, const char* target = NULL) {
  if (idx < 0 || idx >= KEY_COUNT) return;
  safeCopy(keymap[idx].action, sizeof(keymap[idx].action), action);
  keymap[idx].entity[0] = '\0';
  keymap[idx].target[0] = '\0';
  if (entity && entity[0]) {
    safeCopy(keymap[idx].entity, sizeof(keymap[idx].entity), entity);
  }
  if (target && target[0]) {
    safeCopy(keymap[idx].target, sizeof(keymap[idx].target), target);
  }
}

// Index of a key name as used in the JSON map, or -1 if unknown.
int keyNameToIndex(const char* name) {
  if (!name || !name[0]) return -1;
  if (strcmp(name, "enc_up") == 0) return KEY_ENC_UP;
  if (strcmp(name, "enc_down") == 0) return KEY_ENC_DOWN;
  if (name[0] == 'r' && name[2] == 'c' && name[3] >= '0' && name[3] <= '3') {
    int r = name[1] - '0';
    int c = name[3] - '0';
    if (r >= 0 && r < 3 && c >= 0 && c < 4) return r * 4 + c;
  }
  return -1;
}

bool bindingIs(const KeyBinding& b, const char* action) {
  return strcmp(b.action, action) == 0;
}

void loadDefaultKeymap() {
  for (int i = 0; i < KEY_COUNT; i++) setBinding(i, "none");
  setBinding(BTN_HOME_R * 4 + BTN_HOME_C,         "home");
  setBinding(BTN_BACK_R * 4 + BTN_BACK_C,         "back");
  setBinding(BTN_SETTINGS_R * 4 + BTN_SETTINGS_C, "settings");
  setBinding(BTN_ENTER_R * 4 + BTN_ENTER_C,       "enter");
  setBinding(BTN_ENTER2_R * 4 + BTN_ENTER2_C,     "enter");   // encoder press
  setBinding(KEY_ENC_UP,   "scroll");
  setBinding(KEY_ENC_DOWN, "scroll");
}

// Apply a key map received from Home Assistant. Unknown keys are ignored and
// any key the message does not mention keeps its current binding, so a partial
// map can never brick the controls.
void applyKeymap(const char* json) {
  JsonDocument doc;
  if (deserializeJson(doc, json)) { DBG("keymap: parse failed\n"); return; }
  JsonObject km = doc["keymap"].as<JsonObject>();
  if (km.isNull()) { DBG("keymap: no keymap object\n"); return; }
  int applied = 0;
  for (JsonPair kv : km) {
    int idx = keyNameToIndex(kv.key().c_str());
    if (idx < 0) continue;
    JsonObject o = kv.value().as<JsonObject>();
    if (o.isNull()) continue;
    setBinding(idx, o["action"] | "none", o["entity"] | "", o["target_page"] | "");
    applied++;
  }
  DBG("keymap applied: "); DBG(applied); DBG("\n");
}

bool indicatorStateM = false;
bool indicatorStateW = false;
bool indicatorStateTrying = false;

const unsigned long ACTIVE_TIMEOUT_MS  = 60000;
const unsigned long WIFI_TIMEOUT_MS    = 12000;
const unsigned long MQTT_TIMEOUT_MS    = 8000;
const unsigned long WDT_TIMEOUT_MS     = 20000;
const unsigned long DRAW_GATE_MS       = 100;
const unsigned long LOADING_RETRY_MS   = 4000;

// [SCROLL-HOLD] Immediately after entering a new page the rotary encoder can
// emit a stray +1 tick (mechanical coupling between the push-switch and the
// detent shaft, or a spurious input edge right after wake). Applied blindly,
// that ghost tick lands on the fresh page and makes every navigation look
// like it "jumped one item further down". Suppress scroll input for a short
// window after every page change so each page is entered cleanly on its
// first row. Enter/back/home/navigate/press bindings are unaffected.
const unsigned long PAGE_SCROLL_HOLD_MS = 250;
unsigned long suppressScrollUntilMs = 0;

// Forward declarations
void parsePageJson(const char* json);
void requestDraw();
void startWifiPortal();
void goBack();
void goHome();
void activateItem();
void publishPowerState();

// -----------------------------------------------------------------------------
// Page cache
// -----------------------------------------------------------------------------
int findCachedIndex(const char* id) {
  for (int i = 0; i < pageCacheCount; i++) {
    if (pageCache[i].id == id) return i;
  }
  return -1;
}

// Stores/updates the raw page JSON for `id`. Returns true if this is new
// content (page wasn't cached yet, or differs byte-for-byte from what was
// cached before) - false if the server just confirmed what we already had.
bool putCache(const char* id, const char* json) {
  int idx = findCachedIndex(id);
  if (idx < 0) {
    if (pageCacheCount < MAX_CACHED_PAGES) {
      idx = pageCacheCount++;
    } else {
      // Cache full: evict the oldest slot. Rare in practice (24 pages is a
      // lot for this pad), and worst case just costs one extra round trip
      // next time that page is opened.
      idx = 0;
      DBG("page cache full, evicting oldest\n");
    }
    pageCache[idx].id = id;
    pageCache[idx].json = json;
    return true;
  }
  if (pageCache[idx].json == json) return false;
  pageCache[idx].json = json;
  return true;
}

void openLoading(const char* id) {
  inEditMode = false;
  editItemIndex = -1;
  suppressScrollUntilMs = millis() + PAGE_SCROLL_HOLD_MS;

  int idx = findCachedIndex(id);
  if (idx >= 0) {
    // We've seen this page before (boot-time fetch or an earlier visit):
    // show it immediately instead of a blank "Loading..." screen. We still
    // mark ourselves as loading so the retry timer below keeps nudging the
    // server for a fresh copy; applyPageUpdate()/mqttCallback() will swap
    // the cache - and the display - the moment the server's answer differs.
    applyPageUpdate(pageCache[idx].json.c_str());
    loadingPage = true;
    pageRequestSentMs = millis();
    return;
  }

  // No cached copy yet - fall back to the old blank "Loading..." behaviour
  // until the server answers for the first time.
  safeCopy(currentPage.id, sizeof(currentPage.id), id);
  currentPage.title[0] = '\0';
  currentPage.parent[0] = '\0';
  currentPage.itemCount = 0;
  currentPage.selected = 0;
  currentPage.scrollOffset = 0;
  loadingPage = true;
  pageRequestSentMs = millis();
  requestDraw();
}

// -----------------------------------------------------------------------------
// Matrix / input
// -----------------------------------------------------------------------------
bool rising(int r, int c) { return sw[r][c] && !swPrev[r][c]; }

void commitStates() {
  for (int r = 0; r < 3; r++)
    for (int c = 0; c < 4; c++) swPrev[r][c] = sw[r][c];
}

void scanMatrix() {
  for (int r = 0; r < 3; r++) {
    for (int k = 0; k < 3; k++) digitalWrite(ROWS[k], HIGH);
    digitalWrite(ROWS[r], LOW);
    delayMicroseconds(80);
    for (int c = 0; c < 4; c++) {
      bool pressed = (digitalRead(COLS[c]) == LOW);
      if (pressed) {
        if (dc[r][c] < 2) dc[r][c]++;
        if (dc[r][c] >= 2) sw[r][c] = true;
      } else {
        dc[r][c] = 0;
        sw[r][c] = false;
      }
    }
  }
  // Leave ALL rows high again, otherwise the last row (ROWS[2] = GPIO45)
  // stays LOW for the whole rest of the loop iteration. A held key on that
  // row would then keep its column LOW permanently, which corrupts the
  // debounce state, fires spurious column ISRs (keyEvents drift), and can
  // even re-trigger actviteItem() or a sleep/wake loop on the next pass.
  for (int k = 0; k < 3; k++) digitalWrite(ROWS[k], HIGH);
}

void IRAM_ATTR onColumn() { keyEvents++; }

void IRAM_ATTR onEncoder() {
  uint8_t cur = (digitalRead(ENC_A) << 1) | digitalRead(ENC_B);
  encoderTicks += ENC_TABLE[(encPrev << 2) | cur];
  encPrev = cur;
}

void resyncEncoder() { encPrev = (digitalRead(ENC_A) << 1) | digitalRead(ENC_B); }

// -----------------------------------------------------------------------------
// Sleep
// -----------------------------------------------------------------------------
bool anyKeyPressed() {
  for (int r = 0; r < 3; r++) {
    for (int k = 0; k < 3; k++) digitalWrite(ROWS[k], HIGH);
    digitalWrite(ROWS[r], LOW);
    delayMicroseconds(80);
    for (int c = 0; c < 4; c++) if (digitalRead(COLS[c]) == LOW) return true;
  }
  return false;
}

void detachInputInterrupts() {
  for (int c = 0; c < 4; c++) detachInterrupt(digitalPinToInterrupt(COLS[c]));
  detachInterrupt(digitalPinToInterrupt(ENC_A));
  detachInterrupt(digitalPinToInterrupt(ENC_B));
}

void attachInputInterrupts() {
  for (int c = 0; c < 4; c++) attachInterrupt(digitalPinToInterrupt(COLS[c]), onColumn, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ENC_A), onEncoder, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ENC_B), onEncoder, CHANGE);
}

void prepareInputsForSleep() {
  for (int r = 0; r < 3; r++) {
    pinMode(ROWS[r], OUTPUT);
    digitalWrite(ROWS[r], LOW);
  }
  for (int c = 0; c < 4; c++) pinMode(COLS[c], INPUT_PULLUP);
  // The encoder pins are wake sources too. They MUST be pulled up
  // explicitly here: a floating pin would keep re-triggering the wake
  // condition and bounce the pad in and out of sleep, which looks exactly
  // like a device that has hung.
  pinMode(ENC_A, INPUT_PULLUP);
  pinMode(ENC_B, INPUT_PULLUP);
}


// -----------------------------------------------------------------------------
// USB power detection
// -----------------------------------------------------------------------------
// When the pad is plugged into a USB host (PC/laptop) it must NOT fall asleep:
// the user is likely flashing / developing and expects the device to stay
// responsive. tud_cdc_n_connected(0) (TinyUSB) is true when a real USB
// host has attached the USB-CDC device; a plain phone charger without data
// lines reports false and the pad keeps its normal sleep behaviour.
bool usbHostAttached() {
#if ARDUINO_USB_CDC_ON_BOOT
#if defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE
  // Hardware USB-Serial-JTAG build: HWCDCSerial is true once a real USB
  // host opened the CDC link (usb_serial_jtag_is_connected). A plain phone
  // charger without data lines reports false, so the battery-friendly
  // sleep behaviour stays intact.
  // Serial.isPlugged() uses the IDF timer-based check (usb_serial_jtag_
  // is_connected): true whenever a real USB host ENABLED the port (SOF
  // running), even with no serial monitor open. (bool)HWCDCSerial alone
  // needs an ACTIVE CDC session - a pad plugged into a PC without a
  // terminal would sleep on USB power and stall on wake with a long
  // "Trying" phase. A plain phone charger reports false in both, so
  // battery sleep stays intact.
  return Serial.isPlugged() || (bool)HWCDCSerial;
#else
  // TinyUSB build: tud_cdc_n_connected(0) is true when a real USB host has
  // attached the USB-CDC device; USBSerial additionally covers "serial
  // monitor open". A plain phone charger without data lines reports false,
  // so the normal battery-friendly sleep behaviour stays intact.
  return tud_cdc_n_connected(0) || (bool)USBSerial;
#endif
#else
  return false;
#endif
}

// Returns true when there is USB power available (VBUS 5V delivered via the USB host connection).
// Same hardware signal as usbHostAttached() — renamed for honesty: the firmware treats a USB host
// connection as "powered" because the VBUS rail it pulls 5V from is what the on-board charge IC
// (separate from this ESP32) draws from. When this is true, even an idle pad should NOT light-sleep,
// because macros fired from a powered-down pad feel laggy on wake.
bool isPowered() { return usbHostAttached(); }

void enterLightSleep() {
  // KEEP the WiFi association and the MQTT session across light sleep.
  // Tearing the radio down (the previous behaviour) meant every wake had to
  // re-associate, get DHCP and redo the MQTT handshake before anything could
  // happen - 1-3 seconds of dead time after each idle period, which is
  // exactly what made the pad feel slow. With modem sleep enabled we still
  // save power during the sleep, but the wake is immediate.
  WiFi.setSleep(true);

  prepareInputsForSleep();

  // Wait for keys to be released
  unsigned long t = millis();
  while (anyKeyPressed() && millis() - t < 2000) delay(10);

  detachInputInterrupts();

  // The render task blocks on a notification when idle, but suspend it for
  // the duration of the sleep anyway so nothing can touch the SPI bus. Drop
  // any pending request first and make sure no refresh is mid-flight.
  renderRequested = false;
  unsigned long rw = millis();
  while (renderBusy && millis() - rw < 1000) delay(1);
  if (renderTaskHandle != NULL) vTaskSuspend(renderTaskHandle);

  // The task watchdog would keep running during light sleep: any sleep
  // longer than WDT_TIMEOUT_MS would panic-reboot the pad. Remove ourselves
  // from the WDT before sleeping and re-add right after waking up.
  esp_err_t wdtDel = esp_task_wdt_delete(NULL);
  if (wdtDel != ESP_OK) DBG("wdt delete failed\n");

  esp_sleep_enable_gpio_wakeup();
  for (int c = 0; c < 4; c++) gpio_wakeup_enable((gpio_num_t)COLS[c], GPIO_INTR_LOW_LEVEL);

  // Encoder wake on level change from current state
  gpio_int_type_t encA_level = digitalRead(ENC_A) == HIGH ? GPIO_INTR_LOW_LEVEL : GPIO_INTR_HIGH_LEVEL;
  gpio_int_type_t encB_level = digitalRead(ENC_B) == HIGH ? GPIO_INTR_LOW_LEVEL : GPIO_INTR_HIGH_LEVEL;
  gpio_wakeup_enable((gpio_num_t)ENC_A, encA_level);
  gpio_wakeup_enable((gpio_num_t)ENC_B, encB_level);

  DBG("light sleep\n");
  int64_t sleepStartUs = esp_timer_get_time();
  esp_light_sleep_start();

  esp_sleep_wakeup_cause_t cause = esp_sleep_get_wakeup_cause();
  DBG("wake cause: "); DBG((int)cause); DBG("\n");

  // Back to full speed: modem sleep off so the next MQTT packet goes out
  // immediately, and the render task running again.
  WiFi.setSleep(false);
  if (renderTaskHandle != NULL) vTaskResume(renderTaskHandle);

  attachInputInterrupts();

  // Back from sleep: re-arm the watchdog as fast as possible.
  esp_err_t wdtAdd = esp_task_wdt_add(NULL);
  if (wdtAdd != ESP_OK) DBG("wdt re-add failed\n");
  resyncEncoder();
  lastInputMs = millis();

  // The broker drops a client that stops sending keepalives while we sleep,
  // so re-check the session right away. WiFi is still up, so a reconnect
  // costs ~100 ms instead of seconds.
  //
  // For a sleep that outlived the MQTT keepalive (30 s; the 60 s idle
  // timeout makes that essentially every sleep) the broker has already closed
  // the session, but the local TCP socket can still report "connected".
  // Trusting that stale socket makes the first publish after wake stall on
  // the ~1 s socket timeout and THEN reconnect - the multi-second "first key
  // press does nothing" lag. Drop the session outright: the next loop pass
  // reconnects instantly (WiFi is still associated) and the press that woke
  // us goes out right away instead of only after a dead-socket timeout. For
  // short sleeps (< keepalive) the session is still valid, so leave it alone.
  if (esp_timer_get_time() - sleepStartUs >= 30 * 1000000) {
    if (mqtt.connected()) mqtt.disconnect();
    mqttConnected = false;
  }
  nextMqttAttemptMs = 0;   // allow an immediate (throttled) reconnect attempt

  // Wake-loop brake: bouncy keys, floating encoder pins or USB activity can
  // re-trigger a wake almost immediately. Without this the pad would ping-
  // pong between sleep and wake and look completely dead. Force a minimum
  // awake window so input is always serviced after a wake.
  minAwakeUntilMs = millis() + MIN_AWAKE_MS;
}

// -----------------------------------------------------------------------------
// Display
// -----------------------------------------------------------------------------
void landPixel(int lx, int ly, uint16_t color) { display.drawPixel(127 - ly, lx, color); }

void landFillRect(int lx, int ly, int lw, int lh, uint16_t color) {
  display.fillRect(127 - (ly + lh - 1), lx, lh, lw, color);
}

void stampText(const char* text, const GFXfont* font, int lx, int ly, bool invert) {
  canvas.setFont(font);
  canvas.setTextSize(1);
  canvas.setTextColor(1);
  canvas.fillScreen(0);
  canvas.setCursor(0, 30);
  canvas.print(text);
  int16_t x1, y1; uint16_t w, h;
  canvas.getTextBounds(text, 0, 30, &x1, &y1, &w, &h);
  for (int j = 0; j < (int)h; j++) {
    for (int i = 0; i < (int)w; i++) {
      if (canvas.getPixel(x1 + i, y1 + j)) {
        int nx = 127 - (ly + y1 + j);
        int ny = lx + x1 + i;
        if (nx >= 0 && nx < 128 && ny >= 0 && ny < 296) {
          display.drawPixel(nx, ny, invert ? GxEPD_WHITE : GxEPD_BLACK);
        }
      }
    }
  }
}

int baselineCentered(int bandCenterY, const char* text, const GFXfont* font) {
  int16_t x1, y1; uint16_t w, h;
  canvas.setFont(font); canvas.setTextSize(1); canvas.setTextColor(1);
  canvas.getTextBounds(text, 0, 30, &x1, &y1, &w, &h);
  return bandCenterY - (y1 + (int)h / 2);
}

void stampCentered(const char* text, const GFXfont* font, int centerY, bool invert) {
  int16_t x1, y1; uint16_t w, h;
  canvas.setFont(font); canvas.setTextSize(1); canvas.setTextColor(1);
  canvas.getTextBounds(text, 0, 30, &x1, &y1, &w, &h);
  int lx = (296 - (int)w) / 2 - x1;
  int ly = baselineCentered(centerY, text, font);
  stampText(text, font, lx, ly, invert);
}

void drawPage(const RenderSnapshot& R) {
  // Reads ONLY its own snapshot (which the main loop is not writing any
  // more), so the input loop keeps running while this ~450 ms refresh is in
  // flight.
  display.setPartialWindow(0, 0, 128, 296);
  display.firstPage();
  do {
    display.fillScreen(GxEPD_WHITE);

    if (R.portal) {
      stampCentered("WiFi Setup", &FreeMonoBold12pt7b, 15, false);
      stampCentered("AP: MicroPad-Setup", &FreeMonoBold9pt7b, 45, false);
      stampCentered("Pass: micropad123", &FreeMonoBold9pt7b, 70, false);
      stampCentered("Open 192.168.4.1", &FreeMonoBold9pt7b, 95, false);
      stampCentered("BACK = exit", &FreeMonoBold9pt7b, 115, false);
      continue;
    }

    landFillRect(0, 0, 296, 22, GxEPD_BLACK);
    int titleBase = baselineCentered(11, R.page.title, &FreeMonoBold9pt7b);
    stampText(R.page.title, &FreeMonoBold9pt7b, 4, titleBase, true);

    if (R.indM) {
      landFillRect(282, 5, 8, 8, GxEPD_WHITE);
    } else if (R.indW) {
      landFillRect(282, 5, 8, 8, GxEPD_BLACK);
      landFillRect(284, 7, 4, 4, GxEPD_WHITE);
    } else if (R.indT) {
      landFillRect(282, 5, 3, 3, GxEPD_WHITE);
      landFillRect(287, 10, 3, 3, GxEPD_WHITE);
    }

    // Power-on indicator: own fixed slot, left of the 282..290 status cell.
    // Drawn as white rects - the old bolt used the FreeMonoBold9pt7b glyph
    // (U+26A1 is not in that font) NON-inverted on the black bar, so it was
    // invisible AND hidden by the MQTT/WiFi priority chain above. Rect-based
    // it always shows whenever USB power is present (classic "power" icon:
    // ring + vertical line). Restored from GitHub main (PR #5 / dc95978) -
    // the v6 drop was based on an older tree and had regressed this.
    if (R.indP) {
      const int LX = 270, LY = 4;
      landFillRect(LX + 1, LY + 0, 6, 1, GxEPD_WHITE); // ring top
      landFillRect(LX + 1, LY + 7, 6, 1, GxEPD_WHITE); // ring bottom
      landFillRect(LX + 0, LY + 1, 1, 6, GxEPD_WHITE); // ring left
      landFillRect(LX + 7, LY + 1, 1, 6, GxEPD_WHITE); // ring right
      landFillRect(LX + 3, LY + 2, 2, 5, GxEPD_WHITE); // power line (gap at top)
    }

    const int ITEM_H = 24;
    const int VISIBLE = 4;
    const int START_Y = 28;

    int scrollOffset = R.page.scrollOffset;

    if (R.page.selected < scrollOffset) scrollOffset = R.page.selected;
    if (R.page.selected >= scrollOffset + VISIBLE) scrollOffset = R.page.selected - VISIBLE + 1;

    if (R.page.itemCount == 0) {
      stampCentered(R.loading ? "Loading..." : "(empty page)", &FreeMonoBold9pt7b, 64, false);
    }

    for (int i = 0; i < VISIBLE && (scrollOffset + i) < R.page.itemCount; i++) {
      int idx = scrollOffset + i;
      int y = START_Y + i * ITEM_H;
      bool sel = (idx == R.page.selected);

      if (sel) landFillRect(2, y, 292, ITEM_H - 2, GxEPD_BLACK);
      else landFillRect(2, y + ITEM_H - 2, 292, 1, GxEPD_LIGHTGREY);

      char line[64];
      const MenuItem& it = R.page.items[idx];
      const char* st  = it.state[0] ? it.state : "";
      const char* val = it.value[0] ? it.value : st;

      // Which reading (if any) is appended as "Name: value".
      //   light/switch  -> state  (on/off)
      //   number        -> value  (setpoint, editable)
      //   media_player  -> value  (e.g. volume) or state
      //   sensor        -> state  (read-only readout, unit included by the
      //                            generator when one is configured)
      // Anything without a reading (category, script, button, settings, back)
      // simply shows its name.
      const char* shown = "";
      if (strcmp(it.type, "light") == 0 || strcmp(it.type, "switch") == 0) {
        shown = st;
      } else if (strcmp(it.type, "sensor") == 0) {
        shown = st;
      } else if (strcmp(it.type, "number") == 0 || strcmp(it.type, "media_player") == 0) {
        shown = val;
      }

      if (shown[0]) {
        // The 9pt font fits roughly 26 characters across the 296 px panel.
        // Shorten the NAME rather than the reading, so the value - the part
        // that actually changes - stays readable.
        const int MAX_CHARS = 26;
        int vlen = (int)strlen(shown);
        int room = MAX_CHARS - vlen - 2;
        if (room < 6) room = 6;
        char nm[40];
        snprintf(nm, sizeof(nm), "%.*s", room, it.name);
        snprintf(line, sizeof(line), "%s: %s", nm, shown);
      } else {
        snprintf(line, sizeof(line), "%s", it.name);
      }

      int base = baselineCentered(y + (ITEM_H - 2) / 2, line, &FreeMonoBold9pt7b);
      stampText(line, &FreeMonoBold9pt7b, 6, base, sel);
    }

    if (R.page.itemCount > VISIBLE) {
      int barH = 80 * VISIBLE / R.page.itemCount;
      int barY = 28 + (80 - barH) * scrollOffset / (R.page.itemCount - VISIBLE);
      landFillRect(288, 28 + barY, 4, barH, GxEPD_BLACK);
    }

    if (R.inEdit && R.editIdx >= 0) {
      landFillRect(20, 40, 256, 48, GxEPD_BLACK);
      char buf[48];
      snprintf(buf, sizeof(buf), "%s: %.0f", R.page.items[R.editIdx].name, R.editVal);
      int base = baselineCentered(64, buf, &FreeMonoBold12pt7b);
      stampText(buf, &FreeMonoBold12pt7b, 30, base, true);
    }

  } while (display.nextPage());
}

// -----------------------------------------------------------------------------
// Render task + requestDraw
// -----------------------------------------------------------------------------
// Small settling gap between two panel refreshes. GxEPD2's own busy-wait
// already blocks until the previous update finished, so this is only a short
// safety margin before driving the controller again. Keep it small: every ms
// here is felt directly as scroll latency.
const unsigned long MIN_REFRESH_GAP_MS = 120;
unsigned long lastRefreshEndMs = 0;

void requestDraw() {
  // Non-blocking: the caller (main loop) owns all page state, so it can fill
  // a snapshot without interfering with the render task. The write always
  // targets the buffer the task is NOT currently drawing (renderDrawIdx),
  // and — if the task is idle — the one it is not about to read. Hand-off is
  // race-free without any locking. Rapid input therefore coalesces into as
  // many refreshes as the panel can physically do, and never blocks the loop.
  //
  // [FIX-3] This used to also compute a "dirty rows" bitmask (indicator
  // flip / page change / item change / selection move) on every single
  // call, including a 20-item loop with 5 strcmp() each. drawPage() has
  // never actually consumed that bitmask - the partial-refresh path it was
  // built for was reverted (see the historic comments removed from this
  // function and from drawPage()/renderTaskLoop()) - so the whole
  // computation ran on every key press and encoder tick for no benefit.
  // It has been removed; the panel is always fully redrawn, exactly as it
  // already was in practice.
  int w;
  if (renderBusy) w = 1 - renderDrawIdx;          // never the one being drawn
  else            w = 1 - renderReadIdx;          // never the last published
  RenderSnapshot& S = renderBuf[w];

  memcpy(&S.page, &currentPage, sizeof(MenuPage));
  S.loading = loadingPage;
  S.indM = indicatorStateM;
  S.indW = indicatorStateW;
  S.indT = indicatorStateTrying;
  S.indP = isPowered();
  // F-2: publish micropad/power heartbeat whenever the powered state flips.
  // publishPowerState() is internally rate-limited (state-change only), so
  // calling it every render pass is fine — at most one retained MQTT publish
  // per VBUS edge. No-op while mqtt is disconnected.
  publishPowerState();
  S.inEdit = inEditMode;
  S.editIdx = editItemIndex;
  S.editVal = editValue;
  S.portal = showPortalScreen;
  S.dirty_rows = 0;        // unused - full refresh only, see comment above
  renderReadIdx = w;      // publish (only now is the buffer read by the task)
  renderRequested = true;
  // Wake the render task explicitly. Previously it polled with delay(1),
  // which never let core 0 idle and could prevent light sleep from engaging.
  if (renderTaskHandle != NULL) xTaskNotifyGive(renderTaskHandle);
}

void renderTaskLoop(void* param) {
  for (;;) {
    if (!renderRequested) {
      // Block until requestDraw() notifies us. The timeout is a safety net
      // only: it guarantees a missed notification can never wedge the
      // display, while normal operation costs zero CPU when idle - which is
      // what allows the chip to enter light sleep cleanly.
      ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(2000));
      if (!renderRequested) continue;
    }
    // Never start a refresh straight after the previous one: give the panel
    // controller its required recovery time, otherwise it can latch up and
    // freeze the screen. Requests that arrive during the wait are simply
    // coalesced into this next refresh.
    if (lastRefreshEndMs != 0) {
      unsigned long since = millis() - lastRefreshEndMs;
      if (since < MIN_REFRESH_GAP_MS) {
        vTaskDelay(pdMS_TO_TICKS(MIN_REFRESH_GAP_MS - since));
      }
    }
    renderRequested = false;
    // [FIX-2] Race condition fix: renderDrawIdx MUST be updated before
    // renderBusy is set to true. requestDraw() (running concurrently on the
    // other core) reads `renderBusy` first and, if true, picks its write
    // buffer as `1 - renderDrawIdx`. With the old ordering
    // (renderBusy = true; renderDrawIdx = renderReadIdx;) there was a
    // window where renderBusy was already true but renderDrawIdx still
    // pointed at the PREVIOUS frame - a requestDraw() landing in that
    // window could pick the buffer this task was about to start reading
    // from a moment later, producing a rare torn/garbled frame. Updating
    // renderDrawIdx first closes that window.
    renderDrawIdx = renderReadIdx;
    renderBusy = true;
    const RenderSnapshot& RSP = renderBuf[renderDrawIdx];

    // The panel is always fully redrawn (the historic F-3 partial-band
    // refresh experiment was reverted - see drawPage() - it produced wrong
    // geometry and visible seams). This is the proven stable path.
    drawPage(RSP);

    renderBusy = false;
    lastRefreshEndMs = millis();
    lastDrawMs = millis();
  }
}

// -----------------------------------------------------------------------------
// MQTT
// -----------------------------------------------------------------------------
// [FIX-4] Applies an ALREADY-PARSED page document to the live state
// (currentPage). Split out of applyPageUpdateData() so mqttCallback() -
// which already has to parse the incoming payload once to read page_id and
// check the prediction-confirm shortcut - can reuse that same JsonDocument
// instead of parsing the identical payload a second time.
// With doDraw=true (default for real navigation) a render is requested;
// with doDraw=false the data is stored but the panel is NOT refreshed -
// used when the server merely confirms what the optimistic prediction
// already shows.
void applyPageUpdateDoc(JsonDocument& doc, bool doDraw) {
  bool samePage = (strcmp(currentPage.id, doc["page_id"] | currentPage.id) == 0);
  safeCopy(currentPage.id,     sizeof(currentPage.id),     doc["page_id"] | currentPage.id);
  safeCopy(currentPage.title,  sizeof(currentPage.title),  doc["title"]   | currentPage.title);
  safeCopy(currentPage.parent, sizeof(currentPage.parent), doc["parent"]  | currentPage.parent);

  int oldSel = currentPage.selected;
  int oldScroll = currentPage.scrollOffset;
  if (!samePage) { currentPage.selected = 0; currentPage.scrollOffset = 0; suppressScrollUntilMs = millis() + PAGE_SCROLL_HOLD_MS; }

  JsonArray arr = doc["items"].as<JsonArray>();
  int idx = 0;
  for (JsonObject item : arr) {
    if (idx >= 20) break;
    MenuItem& it = currentPage.items[idx];
    safeCopy(it.name,        sizeof(it.name),        item["name"]   | "?");
    safeCopy(it.type,        sizeof(it.type),        item["type"]   | "generic");
    safeCopy(it.entity,      sizeof(it.entity),      item["entity"] | "");
    safeCopy(it.state,       sizeof(it.state),       item["state"]  | "");
    safeCopy(it.value,       sizeof(it.value),       item["value"]  | "");
    safeCopy(it.target_page, sizeof(it.target_page), item["target_page"] | "");
    it.minVal = item["min"] | 0.0f;
    it.maxVal = item["max"] | 255.0f;
    it.step  = item["step"] | 1.0f;
    it.editable = item["editable"] | false;
    idx++;
  }
  currentPage.itemCount = idx;
  loadingPage = false;

  if (samePage) { currentPage.selected = oldSel; currentPage.scrollOffset = oldScroll; }
  else if (currentPage.selected >= currentPage.itemCount && currentPage.itemCount > 0) currentPage.selected = currentPage.itemCount - 1;

  appState = ST_LIST;
  if (doDraw) requestDraw();
}

// Parses `json` once and applies it. Used by callers that only have the raw
// string (page-cache replay, resync retries) - mqttCallback() bypasses this
// and calls applyPageUpdateDoc() directly with the document it already
// parsed, see [FIX-4].
void applyPageUpdateData(const char* json, bool doDraw) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, json);
  if (err) { Serial.print("JSON parse failed: "); Serial.println(err.c_str()); return; }
  lastDisplayedRaw = json;
  applyPageUpdateDoc(doc, doDraw);
}

void applyPageUpdate(const char* json) { applyPageUpdateData(json, true); }

void parsePageJson(const char* json) { applyPageUpdate(json); }

// -----------------------------------------------------------------------------
// Optimistic "predict" helpers
// -----------------------------------------------------------------------------
// The pad flips/updates an item's state/value locally the moment the user
// acts, so the display feels instant instead of waiting for the MQTT round
// trip. The server's reply is authoritative: applyPageUpdate()/mqttCallback()
// overwrites our prediction with the real state if it differs.
//
// To avoid a SECOND ~450 ms refresh for every toggle/edit (predict draw +
// server-confirm draw), we remember what we predicted. If the server reply
// for the same entity carries exactly that expected state/value, it is
// applied to the page data WITHOUT re-rendering - the panel already shows it.
// -----------------------------------------------------------------------------
// Post-action resync
// -----------------------------------------------------------------------------
// Anything that changes a state (toggle/edit) is sent fire-and-forget, while
// the panel already shows the predicted result. If an event or its reply is
// lost anywhere along the way (queue overflow, the HA automation's
// mode: queued limit, an MQTT hiccup), the pad would keep a wrong state
// FOREVER - the page cache has already been updated by the prediction, so no
// later diff would ever notice. Once the user stops acting for a moment we
// therefore ask the server for the current page again and let its answer
// overwrite the display. Cheap, and it makes the pad self-correcting.
const unsigned long RESYNC_SETTLE_MS = 1000;   // quiet time after the last action
const unsigned long RESYNC_RETRY_MS  = 2500;   // spacing between attempts
const int           RESYNC_MAX_ATTEMPTS = 3;
unsigned long lastActionMs = 0;
unsigned long resyncSentMs = 0;
int           resyncLeft   = 0;

// Called by every optimistic action (toggle/edit).
void noteAction() {
  lastActionMs = millis();
  resyncSentMs = 0;
  resyncLeft = RESYNC_MAX_ATTEMPTS;
}

struct PendingPrediction {
  bool active;
  char entity[64];
  char field[16];      // "state" or "value"
  char expected[16];
  uint32_t seq;        // actionSeq at the moment this prediction was made
};
PendingPrediction pendingPred;

// Bumped on every user action. A prediction may only be applied WITHOUT a
// redraw if no further action happened since: otherwise the arriving page is
// the reply to an older action, and silently accepting it would leave the
// panel showing something different from what the server actually has.
uint32_t actionSeq = 0;

void predictionClear() { pendingPred.active = false; }

void predictionSet(const char* entity, const char* field, const char* expected) {
  pendingPred.active = true;
  pendingPred.seq = actionSeq;
  safeCopy(pendingPred.entity, sizeof(pendingPred.entity), entity ? entity : "");
  safeCopy(pendingPred.field, sizeof(pendingPred.field), field);
  safeCopy(pendingPred.expected, sizeof(pendingPred.expected), expected);
}

int findItemIndex(const char* entity) {
  if (!entity || !entity[0]) return -1;
  for (int i = 0; i < currentPage.itemCount; i++) {
    if (currentPage.items[i].entity[0] && strcmp(currentPage.items[i].entity, entity) == 0) return i;
  }
  return -1;
}

// Optimistically set an item on the current page to a known state ("on" /
// "off"). Does nothing if that entity is not on the page - there is simply
// nothing to show then, the event still gets published.
void predictSetState(const char* entity, const char* desired) {
  int i = findItemIndex(entity);
  if (i < 0) return;
  safeCopy(currentPage.items[i].state, sizeof(currentPage.items[i].state), desired);
  actionSeq++;
  predictionSet(entity, "state", desired);
  noteAction();
  requestDraw();
}

// Optimistically flip a light/switch on the current page and redraw.
void predictToggle(const char* entity) {
  int i = findItemIndex(entity);
  if (i < 0) return;
  // on -> off ; anything else (off/unknown/unavailable) -> on
  const char* newState = (strcmp(currentPage.items[i].state, "on") == 0) ? "off" : "on";
  predictSetState(entity, newState);
}

// Optimistically show the edited value for a number/media_player item,
// exit edit mode and redraw.
void predictValue(const char* entity, float v) {
  int i = findItemIndex(entity);
  char buf[16];
  buf[0] = '\0';
  if (i >= 0) {
    // step-aware formatting: whole values unless step is fractional
    MenuItem& it = currentPage.items[i];
    if (fabsf(it.step - (int)it.step) > 0.0001f) snprintf(buf, sizeof(buf), "%.1f", v);
    else snprintf(buf, sizeof(buf), "%.0f", v);
    safeCopy(currentPage.items[i].value, sizeof(currentPage.items[i].value), buf);
  }
  actionSeq++;
  predictionSet(entity, "value", buf);
  noteAction();
  inEditMode = false;
  editItemIndex = -1;
  requestDraw();
}

// Response to our boot-time "get_all_pages" event: a single retained message
// on micropad/pages/all containing every page the server knows about, e.g.
//   {"pages": [ {"page_id":"home", ...}, {"page_id":"lamps", ...}, ... ]}
// Each entry is the exact same per-page object normally sent on
// micropad/page/current, so we just re-serialize each one and drop it into
// the same cache used for single-page updates.
void handleAllPagesPayload(const char* json) {
  JsonDocument doc;
  if (deserializeJson(doc, json)) { Serial.println("pages/all: JSON parse failed"); return; }
  JsonArray pages = doc["pages"].as<JsonArray>();
  int stored = 0;
  int skippedTooLarge = 0;
  for (JsonObject page : pages) {
    const char* pid = page["page_id"] | "";
    if (!pid[0]) continue;
    // [FIX-5] Room for the largest page we can realistically receive. The
    // MQTT buffer (mqtt.setBufferSize()) allows an incoming message up to
    // 16 KB, but this buffer used to be only 2048 B - a page that arrived
    // fine over MQTT could then be silently TRUNCATED when re-serialised
    // here, and the truncated (invalid) JSON was cached as if it were
    // valid, permanently breaking that page until the next boot/refetch.
    // 4096 B comfortably covers the ~1800 B the web generator already
    // targets per page with headroom; pages that still don't fit are now
    // detected and skipped instead of being cached broken.
    char buf[4096];
    size_t n = serializeJson(page, buf, sizeof(buf));
    if (n > 0 && n < sizeof(buf) - 1) {
      putCache(pid, buf);
      stored++;
    } else {
      skippedTooLarge++;
      DBG("page too large to cache, skipped: "); DBG(pid); DBG("\n");
    }
  }
  DBG("pages/all: cached "); DBG(stored); DBG(" page(s)");
  if (skippedTooLarge > 0) { DBG(", skipped "); DBG(skippedTooLarge); DBG(" (too large)"); }
  DBG("\n");

  // If we're still sitting on a blank "Loading..." for a page that just
  // showed up in this batch (e.g. first boot, no per-page reply yet), show
  // it now instead of waiting on the slower single-page round trip.
  if (loadingPage && currentPage.itemCount == 0) {
    int idx = findCachedIndex(currentPage.id);
    if (idx >= 0) applyPageUpdate(pageCache[idx].json.c_str());
  }
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  // PubSubClient reports the STORED length even if it had to truncate the
  // payload at the buffer limit. Writing payload[length]='\0' with a
  // saturated buffer would land ONE byte past the heap buffer -> memory
  // Cap pointless. Anything beyond the buffer is a truncated catalog and
  // can only fail the JSON parse below, never corrupt RAM. Keep in sync
  // with mqtt.setBufferSize().
  if (length > 16380) length = 16380;
  payload[length] = '\0';
  const char* json = (const char*)payload;

  if (strcmp(topic, "micropad/pages/all") == 0) {
    handleAllPagesPayload(json);
    return;
  }
  if (strcmp(topic, "micropad/keymap") == 0) {
    applyKeymap(json);
    return;
  }
  if (strcmp(topic, "micropad/page/current") != 0) return;

  // [FIX-4] Parse the payload exactly once. Everything below that used to
  // call applyPageUpdate(json) - which re-parsed this same string from
  // scratch - now calls applyPageUpdateDoc(idDoc, ...) and reuses this
  // document instead.
  JsonDocument idDoc;
  if (deserializeJson(idDoc, json)) return;
  const char* pid = idDoc["page_id"] | currentPage.id;
  // Accept incoming page if it matches current page OR we are loading/empty
  bool matches = (strcmp(pid, currentPage.id) == 0) || loadingPage || (currentPage.itemCount == 0 && currentPage.title[0] == '\0');
  if (!matches && appState == ST_LIST) return;

  // Prediction-confirm shortcut: if this reply is for the page we are on and
  // contains the exact state/value we optimistically predicted for the item
  // we just acted on, the panel already shows it - applying the data without
  // a second ~450 ms refresh is enough. The cache is still updated so the
  // server stays authoritative for future navigation.
  if (pendingPred.active && pendingPred.seq == actionSeq && strcmp(pid, currentPage.id) == 0) {
    JsonArray arr = idDoc["items"].as<JsonArray>();
    for (JsonObject item : arr) {
      const char* ent = item["entity"] | "";
      if (ent[0] && strcmp(ent, pendingPred.entity) == 0) {
        bool confirmed = false;
        if (strcmp(pendingPred.field, "state") == 0) {
          const char* st = item["state"] | "";
          confirmed = (strcmp(st, pendingPred.expected) == 0);
        } else {
          const char* val = item["value"] | "";
          confirmed = (strcmp(val, pendingPred.expected) == 0);
        }
        if (confirmed) {
          // Same content is already on the panel -> update data + cache only.
          DBG("prediction confirmed, skip redraw\n");
          pendingPred.active = false;
          putCache(pid, json);
          lastDisplayedRaw = json;
          applyPageUpdateDoc(idDoc, false);
          return;
        }
        break;
      }
    }
    // Server disagrees with our prediction (or item vanished): fall through
    // and do a normal authoritative redraw.
    pendingPred.active = false;
  }

  // An authoritative page for the page we are showing arrived: the pad has
  // caught up with the server, so no further post-action resync is needed.
  if (strcmp(pid, currentPage.id) == 0) resyncLeft = 0;

  // The server's copy is authoritative. If it's identical to what we
  // already had cached (e.g. what we just showed instantly from cache in
  // openLoading()), there's nothing new to draw - just clear the loading
  // flag. If it differs (or this page was never cached), store it and
  // (re)draw, so the display always ends up showing the server's version.
  bool changed = putCache(pid, json);
  if (changed || loadingPage) {
    lastDisplayedRaw = json;
    applyPageUpdateDoc(idDoc, true);
  } else {
    loadingPage = false;
  }
}

void publishEvent(const char* action, const char* entity, float value, const char* targetPage) {
  if (pendCount >= PEND_QUEUE_SIZE) {
    // Queue full: drop the oldest rather than the newest, so the most
    // recent user action (or fetch request) always wins.
    DBG("event queue full, dropping oldest pending event\n");
    pendHead = (pendHead + 1) % PEND_QUEUE_SIZE;
    pendCount--;
  }
  PendingEvent& pe = pendQueue[pendTail];
  safeCopy(pe.action, sizeof(pe.action), action);
  pe.entity[0] = '\0';
  if (entity) safeCopy(pe.entity, sizeof(pe.entity), entity);
  pe.hasValue = (value != -9999);
  pe.value = value;
  pe.target[0] = '\0';
  if (targetPage) safeCopy(pe.target, sizeof(pe.target), targetPage);
  pendTail = (pendTail + 1) % PEND_QUEUE_SIZE;
  pendCount++;
}

void flushPending() {
  if (pendCount == 0 || !mqttConnected) return;
  // Drain several queued events per loop pass. Publishing one per pass was
  // too slow for a spam burst: the queue backed up and started dropping
  // events, which is how the pad's optimistic state drifted away from the
  // real Home Assistant state.
  const int MAX_EVENTS_PER_PASS = 4;
  for (int n = 0; n < MAX_EVENTS_PER_PASS && pendCount > 0; n++) {
    PendingEvent& pe = pendQueue[pendHead];
    JsonDocument doc;
    doc["action"] = pe.action;
    if (pe.entity[0]) doc["entity"] = pe.entity;
    if (pe.hasValue) doc["value"] = pe.value;
    if (pe.target[0]) doc["target_page"] = pe.target;
    doc["page_id"] = currentPage.id;
    char buf[256];
    serializeJson(doc, buf);
    bool ok = mqtt.publish("micropad/event", buf);
    DBG("event: "); DBG(buf); DBG("\n");
    if (!ok) break;                     // keep it queued and retry next pass
    pendHead = (pendHead + 1) % PEND_QUEUE_SIZE;
    pendCount--;
  }
}

// -----------------------------------------------------------------------------
// micropad/power heartbeat (F-2)
// -----------------------------------------------------------------------------
// Published to MQTT topic "micropad/power" (retained). Tells HA whether the
// pad is VBUS-powered right now, whether the USB host has attached the CDC
// link, and what millis() reading we last sampled at. vbus_mv is reserved
// for a future ADC-equipped board revision; emitted as JSON null so the
// schema is stable today.
// Rate-limited: only publishes when the powered-state bit flips vs. the
// last sample we sent. Function-static so the throttle survives across
// loop passes without polluting file-scope state.
void publishPowerState() {
  static bool lastPoweredSeen = false;
  bool nowPowered = isPowered();
  if (nowPowered == lastPoweredSeen) return;
  lastPoweredSeen = nowPowered;
  if (!mqttConnected || mqtt.connected() == false) return;
  StaticJsonDocument<192> doc;
  doc["powered"]  = nowPowered;
  doc["usb_host"] = usbHostAttached();
  doc["vbus_mv"]  = nullptr;   // reserved (no ADC equipped)
  doc["since_ms"] = millis();
  char buf[192]; size_t n = serializeJson(doc, buf, sizeof(buf));
  mqtt.publish("micropad/power", (const uint8_t*)buf, n, true);
}

bool connectMqtt() {
  mqtt.setServer(mqttServer, 1883);
  mqtt.setCallback(mqttCallback);
  // Short socket timeout: every MQTT call is executed on the main loop, so a
  // long timeout means one unreachable broker freezes the whole UI for that
  // long. [FIX-6] PubSubClient interprets this value in SECONDS, not
  // milliseconds - so this is really ~1000 ms, not "800 ms" as a previous
  // version of this comment claimed. Still short enough to keep the loop
  // responsive on a LAN.
  mqtt.setSocketTimeout(1);
  mqtt.setKeepAlive(30);
  // The boot-time "pages/all" answer contains EVERY page and easily exceeds
  // 2 KB (currently ~6 KB with a full menu tree; per-page effective keymap
  // overlay pushes the worst case higher). PubSubClient does NOT drop an
  // oversized message: it quietly TRUNCATES the payload at the buffer limit
  // and still calls the callback with the cut-off JSON. The page cache then
  // never gets a valid update and navigation silently shows stale content.
  // Buffer must cover the full catalog: topic+headers eat ~24 B on top of
  // the payload.
  mqtt.setBufferSize(16384);
  if (mqtt.connect("micropad", mqttUser, mqttPass)) {
    mqtt.subscribe("micropad/page/current");
    mqtt.subscribe("micropad/pages/all");
    mqtt.subscribe("micropad/keymap");
    mqttConnected = true;
    return true;
  }
  return false;
}

void startNetwork() {
  if (wifiStarted || appState == ST_WIFI_PORTAL) return;
  WiFi.mode(WIFI_STA);
  // Modem sleep makes the radio buffer/batch packets: every MQTT round trip
  // then costs hundreds of milliseconds extra, which is exactly why opening
  // a page felt slow. The pad is only awake for short bursts and fully
  // disconnects before light sleep, so disabling it costs nothing meaningful
  // in battery life but makes the UI feel instant while in use.
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  WiFi.begin(wifiSSID, wifiPass);
  wifiStarted = true;
  DBG("wifi start: "); DBG(wifiSSID); DBG("\n");
}

// -----------------------------------------------------------------------------
// WiFi Manager
// -----------------------------------------------------------------------------
void scanNetworks() {
  foundNetworkCount = WiFi.scanNetworks();
  if (foundNetworkCount > 20) foundNetworkCount = 20;
  for (int i = 0; i < foundNetworkCount; i++) foundNetworks[i] = WiFi.SSID(i);
}

void handleRoot() {
  scanNetworks();
  String html = "<html><head><meta name='viewport' content='width=device-width, initial-scale=1'>";
  html += "<style>body{font-family:sans-serif;padding:20px}input,select,button{width:100%;padding:12px;margin:8px 0;font-size:16px}</style></head><body>";
  html += "<h2>MicroPad WiFi Setup</h2><form action='/save' method='post'>";
  html += "<label>WiFi Network</label><select name='ssid'>";
  for (int i = 0; i < foundNetworkCount; i++) html += "<option value='" + foundNetworks[i] + "'>" + foundNetworks[i] + "</option>";
  html += "</select><label>Password</label><input type='password' name='pass' placeholder='password'>";
  html += "<label>MQTT Broker IP</label><input type='text' name='mqtt' value='" + String(mqttServer) + "'>";
  html += "<button type='submit'>Connect</button></form></body></html>";
  server.send(200, "text/html", html);
}

void handleSave() {
  prefs.putString("ssid", server.arg("ssid"));
  prefs.putString("pass", server.arg("pass"));
  prefs.putString("mqtt", server.arg("mqtt"));
  server.send(200, "text/html", "<html><body><h2>Saving...</h2><p>Restarting MicroPad.</p></body></html>");
  delay(1000);
  ESP.restart();
}

void startWifiPortal() {
  appState = ST_WIFI_PORTAL;
  pendHead = pendTail = pendCount = 0;
  if (mqttConnected) { mqtt.disconnect(); mqttConnected = false; }
  if (wifiStarted) { WiFi.disconnect(); wifiStarted = false; }
  WiFi.mode(WIFI_AP);
  WiFi.softAP("MicroPad-Setup", "micropad123");
  server.on("/", handleRoot);
  server.on("/save", HTTP_POST, handleSave);
  server.begin();
  portalLastActivityMs = millis();
  showPortalScreen = true;
  requestDraw();
}

// Leave the captive portal (BACK key): if WiFi credentials are already
// stored, reconnect and carry on; otherwise just show an empty list.
void exitPortal() {
  showPortalScreen = false;
  server.close();
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_STA);
  appState = ST_LIST;
  String ssid = prefs.getString("ssid", "");
  if (ssid.length() > 0) {
    strlcpy(wifiSSID, ssid.c_str(), sizeof(wifiSSID));
    strlcpy(wifiPass, prefs.getString("pass", "").c_str(), sizeof(wifiPass));
    wifiStarted = false;
    startNetwork();
  } else {
    // No credentials yet: show an empty home page, nothing crashes.
    currentPage.itemCount = 0;
    safeCopy(currentPage.id, sizeof(currentPage.id), "home");
    safeCopy(currentPage.title, sizeof(currentPage.title), "Home");
    currentPage.parent[0] = '\0';
  }
  requestDraw();
  Serial.println("portal exited");
}

// -----------------------------------------------------------------------------
// Actions
// -----------------------------------------------------------------------------
void goBack() {
  if (inEditMode) { inEditMode = false; editItemIndex = -1; requestDraw(); return; }
  bool toHome = (currentPage.parent[0] == '\0');
  const char* target = toHome ? "home" : currentPage.parent;
  openLoading(target);
  publishEvent(toHome ? "home" : "navigate", NULL, -9999, toHome ? NULL : target);
}

void goHome() {
  inEditMode = false;
  openLoading("home");
  publishEvent("home", NULL, -9999, NULL);
}

void activateItem() {
  if (currentPage.itemCount == 0) return;
  MenuItem& it = currentPage.items[currentPage.selected];
  if (inEditMode) {
    // Exit edit mode FIRST (predictValue handles state+redraw), then publish.
    const char* entity = it.entity;
    const float newVal = editValue;
    predictValue(entity, newVal);          // show the value instantly
    publishEvent("edit", entity, newVal, NULL);
    return;
  }
  if (strcmp(it.type, "category") == 0) {
    openLoading(it.target_page);
    publishEvent("navigate", NULL, -9999, it.target_page);
  }
  else if (strcmp(it.type, "light") == 0 || strcmp(it.type, "switch") == 0) {
    const char* entity = it.entity;
    predictToggle(entity);                 // flip on/off instantly
    publishEvent("toggle", entity, -9999, NULL);
  }
  else if (strcmp(it.type, "script") == 0 || strcmp(it.type, "button") == 0) publishEvent("press", it.entity, -9999, NULL);
  else if (strcmp(it.type, "settings") == 0 || strcmp(it.type, "wifimanager") == 0) startWifiPortal();
  else if (strcmp(it.type, "back") == 0) goBack();
  // Read-only types: a sensor has no writable service, so pressing ENTER on
  // it must not open the edit overlay (which would then publish an "edit"
  // event Home Assistant could not apply).
  else if (strcmp(it.type, "sensor") == 0) { /* display only */ }
  else if (it.editable || strcmp(it.type, "number") == 0 || strcmp(it.type, "media_player") == 0) {
    inEditMode = true;
    editItemIndex = currentPage.selected;
    editValue = atof(it.value[0] ? it.value : it.state);
    requestDraw();
  }
}

// Scrolling requests a refresh on every encoder tick, exactly like the old
// blocking firmware did - no added settle delay. Coalescing is handled by the
// render task instead: a tick only publishes a snapshot (a cheap memcpy) and
// the task always draws the NEWEST snapshot when it becomes free, so a fast
// turn can never build a backlog of stale frames. Any latency here would be
// felt directly on every step, so there is none.
// Move the selection (or the value being edited) - this is what the encoder
// does by default and what the "scroll_up"/"scroll_down" actions reuse.
void applyScroll(int delta) {
  if (inEditMode && editItemIndex >= 0) {
    MenuItem& it = currentPage.items[editItemIndex];
    editValue += delta * it.step;
    if (editValue < it.minVal) editValue = it.minVal;
    if (editValue > it.maxVal) editValue = it.maxVal;
    requestDraw();
  } else {
    int old = currentPage.selected;
    currentPage.selected += delta;
    if (currentPage.selected < 0) currentPage.selected = 0;
    if (currentPage.selected >= currentPage.itemCount) currentPage.selected = currentPage.itemCount - 1;
    if (currentPage.selected != old && currentPage.itemCount > 0) requestDraw();
  }
}

// Run whatever a key is bound to. Every action either changes the display
// locally (and predicts the new state) or publishes an MQTT event that the
// Home Assistant automation turns into a service call.
void executeBinding(const KeyBinding& b, int step) {
  if (b.action[0] == '\0' || bindingIs(b, "none")) return;

  if (bindingIs(b, "enter"))         { activateItem(); return; }
  if (bindingIs(b, "back"))          { goBack(); return; }
  if (bindingIs(b, "home"))          { goHome(); return; }
  if (bindingIs(b, "settings"))      { startWifiPortal(); return; }
  if (bindingIs(b, "scroll"))        { applyScroll(step); return; }
  // Item 0 is drawn at the TOP of the list, so moving the selection UP means
  // DECREASING the index. These two were swapped, which made "Auswahl hoch"
  // scroll down and "Auswahl runter" scroll up.
  // (The encoder keeps its own convention: turning right/CW = index +, i.e.
  // down the list - that is what "scroll" above does.)
  if (bindingIs(b, "scroll_up"))     { applyScroll(-1); return; }
  if (bindingIs(b, "scroll_down"))   { applyScroll(1); return; }

  if (bindingIs(b, "navigate")) {
    if (b.target[0]) { openLoading(b.target); publishEvent("navigate", NULL, -9999, b.target); }
    return;
  }
  if (!b.entity[0]) return;
  if (bindingIs(b, "toggle")) { predictToggle(b.entity);        publishEvent("toggle", b.entity, -9999, NULL); return; }
  if (bindingIs(b, "on"))     { predictSetState(b.entity, "on");  publishEvent("on",  b.entity, -9999, NULL); return; }
  if (bindingIs(b, "off"))    { predictSetState(b.entity, "off"); publishEvent("off", b.entity, -9999, NULL); return; }
  if (bindingIs(b, "press"))  { publishEvent("press", b.entity, -9999, NULL); return; }
  // Media transport actions (Spotify / media_player entities). These are
  // passed through verbatim to HA; the automation dispatches them to
  // media_player.media_play_pause / media_next_track / media_previous_track,
  // volume_up / volume_down.
  if (bindingIs(b, "volume_up"))    { publishEvent("volume_up",     b.entity, -9999, NULL); return; }
  if (bindingIs(b, "volume_down"))  { publishEvent("volume_down",   b.entity, -9999, NULL); return; }
  if (bindingIs(b, "media_next"))   { publishEvent("media_next",    b.entity, -9999, NULL); return; }
  if (bindingIs(b, "media_prev"))   { publishEvent("media_prev",    b.entity, -9999, NULL); return; }
}

void handleInput() {
  int delta = encoderTicks - lastEncoderTicks;
  lastEncoderTicks = encoderTicks;

  // [SCROLL-HOLD] Suppress scroll input inside the post-navigation hold
  // window (see PAGE_SCROLL_HOLD_MS). Discrete and navigation bindings
  // (enter/back/home/navigate/toggle/...) are unaffected.
  bool scrollOpen = (int32_t)(millis() - suppressScrollUntilMs) >= 0;

  if (delta != 0) {
    lastInputMs = millis();
    const KeyBinding& b = (delta > 0) ? keymap[KEY_ENC_UP] : keymap[KEY_ENC_DOWN];
    if (bindingIs(b, "scroll") || b.action[0] == '\0') {
      // Default: move the selection by the full turn delta, so fast turning
      // still scrolls fast. Ignore it inside the hold window (ghost tick).
      if (scrollOpen) applyScroll(delta);
    } else {
      // Bound to a discrete action (volume, toggle, ...): fire once per step,
      // capped so a fast flick cannot flood the event queue.
      int steps = (delta > 0) ? delta : -delta;
      if (steps > 4) steps = 4;
      int sign = (delta > 0) ? 1 : -1;
      for (int k = 0; k < steps; k++) executeBinding(b, sign);
    }
  }

  // Every matrix key now runs whatever Home Assistant bound to it. There are
  // no hard-wired ENTER/BACK/HOME keys any more - the defaults just happen to
  // reproduce the classic layout.
  for (int r = 0; r < 3; r++) {
    for (int c = 0; c < 4; c++) {
      if (rising(r, c)) {
        lastInputMs = millis();
        const KeyBinding& kb = keymap[r * 4 + c];
        // Ignore a scroll_up/scroll_down key that fires inside the hold
        // window (post-wake / post-navigation stray edge).
        if (!scrollOpen && (bindingIs(kb, "scroll_up") || bindingIs(kb, "scroll_down"))) continue;
        executeBinding(kb, 1);
      }
    }
  }
}

// -----------------------------------------------------------------------------
// Setup / Loop
// -----------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  Serial.println("setup");

  for (int r = 0; r < 3; r++) {
    pinMode(ROWS[r], OUTPUT);
    digitalWrite(ROWS[r], HIGH);
  }
  for (int c = 0; c < 4; c++) {
    pinMode(COLS[c], INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(COLS[c]), onColumn, CHANGE);
  }
  pinMode(ENC_A, INPUT_PULLUP);
  pinMode(ENC_B, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(ENC_A), onEncoder, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ENC_B), onEncoder, CHANGE);
  resyncEncoder();

  SPI.begin(39, -1, 38, -1);
  display.init(0);   // 0 = diagnostics OFF (see DISABLE_DIAGNOSTIC_OUTPUT above)
  display.setRotation(0);

  esp_task_wdt_config_t wdtCfg = {};
  wdtCfg.timeout_ms = WDT_TIMEOUT_MS;
  wdtCfg.idle_core_mask = 0;
  wdtCfg.trigger_panic = true;
  esp_err_t e = esp_task_wdt_init(&wdtCfg);
  if (e != ESP_OK) Serial.println("TWDT already init");
  esp_task_wdt_add(NULL);

  prefs.begin("micropad", false);

  // Classic layout until Home Assistant sends its own map.
  loadDefaultKeymap();

  // Boot render task on core 0: it does all e-paper refreshes from now on.
  // The main loop on core 1 keeps scanning input the whole time -> buttons
  // are responsive even mid-refresh, actions can be spammed.
  xTaskCreatePinnedToCore(renderTaskLoop, "renderTask", 8192, NULL, 1, &renderTaskHandle, 0);

  // Initial frame (white list page / portal) before the first MQTT reply.
  safeCopy(currentPage.id, sizeof(currentPage.id), "home");
  safeCopy(currentPage.title, sizeof(currentPage.title), "Home");
  currentPage.parent[0] = '\0';
  currentPage.itemCount = 0;
  indicatorStateTrying = true;
  showPortalScreen = false;
  requestDraw();
  // Let core 0 paint it right away, so the user sees *something* instead of
  // a blank panel during WiFi connect.
  vTaskDelay(pdMS_TO_TICKS(20));

  String savedMqtt = prefs.getString("mqtt", "");
  if (savedMqtt.length() > 0) strlcpy(mqttServer, savedMqtt.c_str(), sizeof(mqttServer));

  String ssid = prefs.getString("ssid", "");
  String pass = prefs.getString("pass", "");

  if (ssid.length() == 0) { startWifiPortal(); return; }
  strlcpy(wifiSSID, ssid.c_str(), sizeof(wifiSSID));
  strlcpy(wifiPass, pass.c_str(), sizeof(wifiPass));

  startNetwork();
  lastInputMs = millis();
}

// The e-paper keeps its image forever and only re-renders when something
// triggers a draw - so unplugging USB would leave the power icon on screen
// until the next input/MQTT event. Poll the VBUS state and ask for a redraw
// on every power edge; the snapshot logic then clears/applies the indicator
// through the normal render path. 500 ms is plenty: the pad is awake
// whenever this matters.
static bool lastPowered = false;
static unsigned long nextPowerPollMs = 0;
void pollPowerIndicator() {
  unsigned long now = millis();
  if (now < nextPowerPollMs) return;
  nextPowerPollMs = now + 500;
  bool p = isPowered();
  if (p != lastPowered) {
    lastPowered = p;
    requestDraw();
  }
}

void loop() {
  esp_task_wdt_reset();
  pollPowerIndicator();

  if (appState == ST_WIFI_PORTAL) {
    // Keep scanning the matrix even in the portal, otherwise the pad is
    // bricked until reflash: BACK must be able to leave the portal again.
    server.handleClient();
    scanMatrix();
    scanMatrix();
    if (rising(BTN_BACK_R, BTN_BACK_C)) {
      commitStates();
      exitPortal();
      return;
    }
    commitStates();
    // Any key/encoder activity resets the portal idle timer, so it won't
    // reboot while the user is interacting.
    int deltaP = encoderTicks - lastEncoderTicks;
    lastEncoderTicks = encoderTicks;
    if (deltaP != 0) portalLastActivityMs = millis();
    for (int r = 0; r < 3; r++)
      for (int c = 0; c < 4; c++)
        if (sw[r][c]) portalLastActivityMs = millis();
    if (millis() - portalLastActivityMs > PORTAL_TIMEOUT_MS) {
      Serial.println("portal timeout, rebooting");
      ESP.restart();
    }
    delay(2);
    return;
  }

  scanMatrix();
  scanMatrix();
  handleInput();
  commitStates();

  bool newM = mqttConnected;
  bool newW = !mqttConnected && (WiFi.status() == WL_CONNECTED);
  bool newTrying = !newM && !newW && wifiStarted;
  if (newM != indicatorStateM || newW != indicatorStateW || newTrying != indicatorStateTrying) {
    indicatorStateM = newM;
    indicatorStateW = newW;
    indicatorStateTrying = newTrying;
    requestDraw();
  }

  unsigned long now = millis();
  bool active = (now - lastInputMs < ACTIVE_TIMEOUT_MS);

  if (active) {
    if (!wifiStarted) startNetwork();
    else if (WiFi.status() != WL_CONNECTED && millis() >= nextWifiAttemptMs) {
      // Auto-reconnect gives up after a while (AP rebooted / out of range):
      // re-kick it, throttled, so a dead radio can never spin the loop.
      nextWifiAttemptMs = millis() + MQTT_RETRY_MS;
      WiFi.begin(wifiSSID, wifiPass);
    }
    if (WiFi.status() == WL_CONNECTED) {
      if (!mqttConnected) {
        // Throttled reconnect: a failed attempt occupies the main loop for
        // up to the socket timeout, so never hammer it - otherwise an
        // unreachable broker freezes the UI in a "block, run, block" loop.
        if (millis() >= nextMqttAttemptMs) {
          if (connectMqtt()) {
            DBG("MQTT connected\n");
            publishEvent("home", NULL, -9999, NULL);
            // Ask for the key map as well. If the automation publishes it
            // retained, the subscription already delivered it - requesting it
            // too costs one tiny message and also covers a non-retained setup.
            publishEvent("keymap", NULL, -9999, NULL);
            if (!didFetchAllPages) {
              // Once per boot: ask the server for every page it knows about,
              // so navigating later can show cached content instantly instead
              // of a blank "Loading..." screen. Re-fetching this on every
              // sleep/wake reconnect isn't needed - the per-page cache stays
              // current via the normal navigate/toggle/edit round trips.
              publishEvent("get_all_pages", NULL, -9999, NULL);
              didFetchAllPages = true;
            }
          } else {
            nextMqttAttemptMs = millis() + MQTT_RETRY_MS;
          }
        }
      } else {
        if (!mqtt.connected()) mqttConnected = false;
        else {
          mqtt.loop();
          flushPending();

          // Post-action resync: once the user has stopped toggling/editing
          // for a moment, pull the authoritative page once more. This repairs
          // any drift caused by a dropped event or a lost reply - without it
          // the pad could stay out of sync with Home Assistant indefinitely.
          if (resyncLeft > 0 && now - lastActionMs >= RESYNC_SETTLE_MS &&
              (resyncSentMs == 0 || now - resyncSentMs >= RESYNC_RETRY_MS)) {
            resyncLeft--;
            resyncSentMs = now;
            predictionClear();          // never let a stale guess swallow this
            if (strcmp(currentPage.id, "home") == 0) publishEvent("home", NULL, -9999, NULL);
            else publishEvent("navigate", NULL, -9999, currentPage.id);
          }

          if (loadingPage && (now - pageRequestSentMs >= LOADING_RETRY_MS)) {
            pageRequestSentMs = now;
            publishEvent("navigate", NULL, -9999, currentPage.id);
          }
        }
      }
    }
  }

  // NOTE: no blocking draw here any more. The e-paper refresh runs on core 0
  // in renderTaskLoop(); this loop only sets renderRequested, so buttons are
  // scanned continuously and presses are never swallowed mid-refresh.

  // Do not sleep while powered via USB host (PC / charger with data lines) -
  // keep the device awake for flashing, development and fast macro response.
  // Only the truly battery-powered case sleeps. F-2 gate now reads isPowered()
  // (the renamed usbHostAttached() — same signal, honest label) so the powered
  // path can flip the modem-sleep off and redraw the indicator when state
  // transitions. The wake-loop brake inside the if-body still applies — a
  // powered pad that just woke up must NOT immediately re-enter sleep on a
  // bouncing pin.
  if (!active && !isPowered()) {
    // Never sleep while a refresh is in flight - the display would be left
    // half-updated (and the SPI bus mid-transaction).
    unsigned long waitStart = millis();
    while (renderBusy && millis() - waitStart < 1000) delay(1);

    // Wake-loop brake: never go straight back to sleep right after a wake,
    // or a bouncy key / floating encoder pin could bounce us between sleep
    // and wake and the pad would look frozen.
    if (millis() >= minAwakeUntilMs) {
      DBG("going to sleep\n");
      enterLightSleep();
    }
  } else if (isPowered()) {
    // Powered path: keep the radio ready (modem sleep OFF so MQTT latency
    // is bounded) and refresh the indicator so the ⚡ glyph appears the
    // instant VBUS rises. requestDraw() is a coalescing flag setter — safe
    // to call every loop pass.
    WiFi.setSleep(false);
    requestDraw();
  }

  delay(2);
}
