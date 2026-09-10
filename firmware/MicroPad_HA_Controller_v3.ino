// =============================================================================
// MicroPad Home Assistant Controller  v6 (Light Sleep, power-optimised)
// - No USB CDC conflicts: light sleep only.
// - WiFi/MQTT disconnected before sleep, full reconnect on wake.
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
// =============================================================================

#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <esp_task_wdt.h>
#include <GxEPD2_BW.h>
#include <Fonts/FreeMonoBold12pt7b.h>
#include <Fonts/FreeMonoBold9pt7b.h>

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
bool dirty = true;
bool loadingPage = false;
String lastDisplayedRaw = "";

struct MenuItem {
  char name[32];
  char type[16];
  char entity[64];
  char state[16];
  char value[16];
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

char mqttServer[64] = "192.168.0.100"; // EDIT: your MQTT broker address
char mqttUser[32]   = "micropad";      // EDIT: your MQTT username
char mqttPass[64]   = "replace-me";    // EDIT: your MQTT password (do not commit real credentials)
char wifiSSID[64]   = "";
char wifiPass[64]   = "";

// Small FIFO instead of a single pending slot: boot now fires two events
// back to back ("home" then "get_all_pages") and a single-slot pending
// buffer would silently drop the first one before flushPending() got to it.
#define PEND_QUEUE_SIZE 4
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

bool indicatorStateM = false;
bool indicatorStateW = false;
bool indicatorStateTrying = false;

const unsigned long ACTIVE_TIMEOUT_MS  = 60000;
const unsigned long WIFI_TIMEOUT_MS    = 12000;
const unsigned long MQTT_TIMEOUT_MS    = 8000;
const unsigned long WDT_TIMEOUT_MS     = 20000;
const unsigned long DRAW_GATE_MS       = 100;
const unsigned long LOADING_RETRY_MS   = 4000;

// Forward declarations
void parsePageJson(const char* json);
void requestDraw();
void startWifiPortal();
void goBack();
void goHome();
void activateItem();

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
      Serial.println("page cache full, evicting oldest");
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
  strncpy(currentPage.id, id, sizeof(currentPage.id));
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
}

void enterLightSleep() {
  if (mqttConnected) { mqtt.disconnect(); mqttConnected = false; }
  if (wifiStarted) { WiFi.disconnect(true, true); wifiStarted = false; }

  prepareInputsForSleep();

  // Wait for keys to be released
  unsigned long t = millis();
  while (anyKeyPressed() && millis() - t < 2000) delay(10);

  detachInputInterrupts();

  esp_sleep_enable_gpio_wakeup();
  for (int c = 0; c < 4; c++) gpio_wakeup_enable((gpio_num_t)COLS[c], GPIO_INTR_LOW_LEVEL);

  // Encoder wake on level change from current state
  gpio_int_type_t encA_level = digitalRead(ENC_A) == HIGH ? GPIO_INTR_LOW_LEVEL : GPIO_INTR_HIGH_LEVEL;
  gpio_int_type_t encB_level = digitalRead(ENC_B) == HIGH ? GPIO_INTR_LOW_LEVEL : GPIO_INTR_HIGH_LEVEL;
  gpio_wakeup_enable((gpio_num_t)ENC_A, encA_level);
  gpio_wakeup_enable((gpio_num_t)ENC_B, encB_level);

  Serial.println("light sleep");
  Serial.flush();
  esp_light_sleep_start();

  esp_sleep_wakeup_cause_t cause = esp_sleep_get_wakeup_cause();
  Serial.print("wake cause: "); Serial.println(cause);

  attachInputInterrupts();
  resyncEncoder();
  lastInputMs = millis();
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

void drawPage() {
  display.setPartialWindow(0, 0, 128, 296);
  display.firstPage();
  do {
    display.fillScreen(GxEPD_WHITE);

    landFillRect(0, 0, 296, 22, GxEPD_BLACK);
    int titleBase = baselineCentered(11, currentPage.title, &FreeMonoBold9pt7b);
    stampText(currentPage.title, &FreeMonoBold9pt7b, 4, titleBase, true);

    if (indicatorStateM) {
      landFillRect(282, 5, 8, 8, GxEPD_WHITE);
    } else if (indicatorStateW) {
      landFillRect(282, 5, 8, 8, GxEPD_BLACK);
      landFillRect(284, 7, 4, 4, GxEPD_WHITE);
    } else if (indicatorStateTrying) {
      landFillRect(282, 5, 3, 3, GxEPD_WHITE);
      landFillRect(287, 10, 3, 3, GxEPD_WHITE);
    }

    const int ITEM_H = 24;
    const int VISIBLE = 4;
    const int START_Y = 28;

    if (currentPage.selected < currentPage.scrollOffset) currentPage.scrollOffset = currentPage.selected;
    if (currentPage.selected >= currentPage.scrollOffset + VISIBLE) currentPage.scrollOffset = currentPage.selected - VISIBLE + 1;

    if (currentPage.itemCount == 0) {
      stampCentered(loadingPage ? "Loading..." : "(empty page)", &FreeMonoBold9pt7b, 64, false);
    }

    for (int i = 0; i < VISIBLE && (currentPage.scrollOffset + i) < currentPage.itemCount; i++) {
      int idx = currentPage.scrollOffset + i;
      int y = START_Y + i * ITEM_H;
      bool sel = (idx == currentPage.selected);

      if (sel) landFillRect(2, y, 292, ITEM_H - 2, GxEPD_BLACK);
      else landFillRect(2, y + ITEM_H - 2, 292, 1, GxEPD_LIGHTGREY);

      char line[48];
      MenuItem& it = currentPage.items[idx];
      const char* st = it.state[0] ? it.state : "";
      if (strcmp(it.type, "light") == 0 || strcmp(it.type, "switch") == 0) {
        snprintf(line, sizeof(line), "%s: %s", it.name, st);
      } else if (strcmp(it.type, "number") == 0 || strcmp(it.type, "media_player") == 0) {
        snprintf(line, sizeof(line), "%s: %s", it.name, it.value[0] ? it.value : st);
      } else {
        snprintf(line, sizeof(line), "%s", it.name);
      }

      int base = baselineCentered(y + (ITEM_H - 2) / 2, line, &FreeMonoBold9pt7b);
      stampText(line, &FreeMonoBold9pt7b, 6, base, sel);
    }

    if (currentPage.itemCount > VISIBLE) {
      int barH = 80 * VISIBLE / currentPage.itemCount;
      int barY = 28 + (80 - barH) * currentPage.scrollOffset / (currentPage.itemCount - VISIBLE);
      landFillRect(288, 28 + barY, 4, barH, GxEPD_BLACK);
    }

    if (inEditMode && editItemIndex >= 0) {
      landFillRect(20, 40, 256, 48, GxEPD_BLACK);
      char buf[48];
      snprintf(buf, sizeof(buf), "%s: %.0f", currentPage.items[editItemIndex].name, editValue);
      int base = baselineCentered(64, buf, &FreeMonoBold12pt7b);
      stampText(buf, &FreeMonoBold12pt7b, 30, base, true);
    }

  } while (display.nextPage());
}

void requestDraw() {
  unsigned long now = millis();
  if (now - lastDrawMs < DRAW_GATE_MS) { dirty = true; return; }
  lastDrawMs = now;
  dirty = false;
  drawPage();
}

// -----------------------------------------------------------------------------
// MQTT
// -----------------------------------------------------------------------------
void applyPageUpdate(const char* json) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, json);
  if (err) { Serial.print("JSON parse failed: "); Serial.println(err.c_str()); return; }

  bool samePage = (strcmp(currentPage.id, doc["page_id"] | currentPage.id) == 0);
  strncpy(currentPage.id, doc["page_id"] | currentPage.id, sizeof(currentPage.id));
  strncpy(currentPage.title, doc["title"] | currentPage.title, sizeof(currentPage.title));
  strncpy(currentPage.parent, doc["parent"] | currentPage.parent, sizeof(currentPage.parent));

  int oldSel = currentPage.selected;
  int oldScroll = currentPage.scrollOffset;
  if (!samePage) { currentPage.selected = 0; currentPage.scrollOffset = 0; }

  JsonArray arr = doc["items"].as<JsonArray>();
  int idx = 0;
  for (JsonObject item : arr) {
    if (idx >= 20) break;
    MenuItem& it = currentPage.items[idx];
    strncpy(it.name, item["name"] | "?", sizeof(it.name));
    strncpy(it.type, item["type"] | "generic", sizeof(it.type));
    strncpy(it.entity, item["entity"] | "", sizeof(it.entity));
    strncpy(it.state, item["state"] | "", sizeof(it.state));
    strncpy(it.value, item["value"] | "", sizeof(it.value));
    strncpy(it.target_page, item["target_page"] | "", sizeof(it.target_page));
    it.minVal = item["min"] | 0.0f;
    it.maxVal = item["max"] | 255.0f;
    it.step  = item["step"] | 1.0f;
    it.editable = item["editable"] | false;
    idx++;
  }
  currentPage.itemCount = idx;
  loadingPage = false;
  lastDisplayedRaw = json;

  if (samePage) { currentPage.selected = oldSel; currentPage.scrollOffset = oldScroll; }
  else if (currentPage.selected >= currentPage.itemCount && currentPage.itemCount > 0) currentPage.selected = currentPage.itemCount - 1;

  appState = ST_LIST;
  requestDraw();
}

void parsePageJson(const char* json) { applyPageUpdate(json); }

// -----------------------------------------------------------------------------
// Optimistic "predict" helpers
// -----------------------------------------------------------------------------
// The pad flips/updates an item's state/value locally the moment the user
// acts, so the display feels instant instead of waiting for the MQTT round
// trip. The server's reply is authoritative: applyPageUpdate()/mqttCallback()
// overwrites our prediction with the real state if it differs.
int findItemIndex(const char* entity) {
  if (!entity || !entity[0]) return -1;
  for (int i = 0; i < currentPage.itemCount; i++) {
    if (currentPage.items[i].entity[0] && strcmp(currentPage.items[i].entity, entity) == 0) return i;
  }
  return -1;
}

// Optimistically flip a light/switch on the current page and redraw.
void predictToggle(const char* entity) {
  int i = findItemIndex(entity);
  if (i < 0) return;
  MenuItem& it = currentPage.items[i];
  // on  -> off ; anything else (off/unknown/unavailable) -> on
  if (strcmp(it.state, "on") == 0) strncpy(it.state, "off", sizeof(it.state));
  else strncpy(it.state, "on", sizeof(it.state));
  requestDraw();
}

// Optimistically show the edited value for a number/media_player item,
// exit edit mode and redraw.
void predictValue(const char* entity, float v) {
  int i = findItemIndex(entity);
  if (i >= 0) {
    char buf[16];
    // step-aware formatting: whole values unless step is fractional
    MenuItem& it = currentPage.items[i];
    if (fabsf(it.step - (int)it.step) > 0.0001f) snprintf(buf, sizeof(buf), "%.1f", v);
    else snprintf(buf, sizeof(buf), "%.0f", v);
    strncpy(currentPage.items[i].value, buf, sizeof(currentPage.items[i].value));
  }
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
  for (JsonObject page : pages) {
    const char* pid = page["page_id"] | "";
    if (!pid[0]) continue;
    char buf[1024];
    size_t n = serializeJson(page, buf, sizeof(buf));
    if (n > 0) { putCache(pid, buf); stored++; }
  }
  Serial.print("pages/all: cached "); Serial.print(stored); Serial.println(" page(s)");

  // If we're still sitting on a blank "Loading..." for a page that just
  // showed up in this batch (e.g. first boot, no per-page reply yet), show
  // it now instead of waiting on the slower single-page round trip.
  if (loadingPage && currentPage.itemCount == 0) {
    int idx = findCachedIndex(currentPage.id);
    if (idx >= 0) applyPageUpdate(pageCache[idx].json.c_str());
  }
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  payload[length] = '\0';
  const char* json = (const char*)payload;

  if (strcmp(topic, "micropad/pages/all") == 0) {
    handleAllPagesPayload(json);
    return;
  }
  if (strcmp(topic, "micropad/page/current") != 0) return;

  JsonDocument idDoc;
  if (deserializeJson(idDoc, json)) return;
  const char* pid = idDoc["page_id"] | currentPage.id;
  // Accept incoming page if it matches current page OR we are loading/empty
  bool matches = (strcmp(pid, currentPage.id) == 0) || loadingPage || (currentPage.itemCount == 0 && currentPage.title[0] == '\0');
  if (!matches && appState == ST_LIST) return;

  // The server's copy is authoritative. If it's identical to what we
  // already had cached (e.g. what we just showed instantly from cache in
  // openLoading()), there's nothing new to draw - just clear the loading
  // flag. If it differs (or this page was never cached), store it and
  // (re)draw, so the display always ends up showing the server's version.
  bool changed = putCache(pid, json);
  if (changed || loadingPage) {
    applyPageUpdate(json);
  } else {
    loadingPage = false;
  }
}

void publishEvent(const char* action, const char* entity, float value, const char* targetPage) {
  if (pendCount >= PEND_QUEUE_SIZE) {
    // Queue full: drop the oldest rather than the newest, so the most
    // recent user action (or fetch request) always wins.
    Serial.println("event queue full, dropping oldest pending event");
    pendHead = (pendHead + 1) % PEND_QUEUE_SIZE;
    pendCount--;
  }
  PendingEvent& pe = pendQueue[pendTail];
  strncpy(pe.action, action, sizeof(pe.action));
  pe.entity[0] = '\0';
  if (entity) strncpy(pe.entity, entity, sizeof(pe.entity));
  pe.hasValue = (value != -9999);
  pe.value = value;
  pe.target[0] = '\0';
  if (targetPage) strncpy(pe.target, targetPage, sizeof(pe.target));
  pendTail = (pendTail + 1) % PEND_QUEUE_SIZE;
  pendCount++;
}

void flushPending() {
  if (pendCount == 0 || !mqttConnected) return;
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
  Serial.print("event: "); Serial.println(buf);
  if (ok) { pendHead = (pendHead + 1) % PEND_QUEUE_SIZE; pendCount--; }
}

bool connectMqtt() {
  mqtt.setServer(mqttServer, 1883);
  mqtt.setCallback(mqttCallback);
  mqtt.setSocketTimeout(3);
  mqtt.setBufferSize(2048);
  if (mqtt.connect("micropad", mqttUser, mqttPass)) {
    mqtt.subscribe("micropad/page/current");
    mqtt.subscribe("micropad/pages/all");
    mqttConnected = true;
    return true;
  }
  return false;
}

void startNetwork() {
  if (wifiStarted || appState == ST_WIFI_PORTAL) return;
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  WiFi.begin(wifiSSID, wifiPass);
  wifiStarted = true;
  Serial.print("wifi start: "); Serial.println(wifiSSID);
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

  display.setPartialWindow(0, 0, 128, 296);
  display.firstPage();
  do {
    display.fillScreen(GxEPD_WHITE);
    stampCentered("WiFi Setup", &FreeMonoBold12pt7b, 15, false);
    stampCentered("AP: MicroPad-Setup", &FreeMonoBold9pt7b, 45, false);
    stampCentered("Pass: micropad123", &FreeMonoBold9pt7b, 70, false);
    stampCentered("Open 192.168.4.1", &FreeMonoBold9pt7b, 95, false);
  } while (display.nextPage());
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
  else if (it.editable || strcmp(it.type, "number") == 0 || strcmp(it.type, "media_player") == 0) {
    inEditMode = true;
    editItemIndex = currentPage.selected;
    editValue = atof(it.value[0] ? it.value : it.state);
    requestDraw();
  }
}

void handleInput() {
  int delta = encoderTicks - lastEncoderTicks;
  lastEncoderTicks = encoderTicks;

  if (delta != 0) {
    lastInputMs = millis();
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

  if (rising(BTN_HOME_R, BTN_HOME_C)) { lastInputMs = millis(); goHome(); }
  if (rising(BTN_BACK_R, BTN_BACK_C)) { lastInputMs = millis(); goBack(); }
  if (rising(BTN_SETTINGS_R, BTN_SETTINGS_C)) { lastInputMs = millis(); startWifiPortal(); }
  if (rising(BTN_ENTER_R, BTN_ENTER_C)) { lastInputMs = millis(); activateItem(); }
  else if (rising(BTN_ENTER2_R, BTN_ENTER2_C)) { lastInputMs = millis(); activateItem(); }
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
  display.init(115200);
  display.setRotation(0);

  esp_task_wdt_config_t wdtCfg = {};
  wdtCfg.timeout_ms = WDT_TIMEOUT_MS;
  wdtCfg.idle_core_mask = 0;
  wdtCfg.trigger_panic = true;
  esp_err_t e = esp_task_wdt_init(&wdtCfg);
  if (e != ESP_OK) Serial.println("TWDT already init");
  esp_task_wdt_add(NULL);

  prefs.begin("micropad", false);

  String savedMqtt = prefs.getString("mqtt", "");
  if (savedMqtt.length() > 0) strlcpy(mqttServer, savedMqtt.c_str(), sizeof(mqttServer));

  String ssid = prefs.getString("ssid", "");
  String pass = prefs.getString("pass", "");

  if (ssid.length() == 0) { startWifiPortal(); return; }
  strlcpy(wifiSSID, ssid.c_str(), sizeof(wifiSSID));
  strlcpy(wifiPass, pass.c_str(), sizeof(wifiPass));

  startNetwork();
  lastInputMs = millis();

  // At cold boot we wait for the retained MQTT page; do not show stale cache
  if (false) {
    display.setPartialWindow(0, 0, 128, 296);
    display.firstPage();
    do {
      display.fillScreen(GxEPD_WHITE);
      stampCentered("Waiting for", &FreeMonoBold12pt7b, 45, false);
      stampCentered("Home Assistant...", &FreeMonoBold9pt7b, 75, false);
    } while (display.nextPage());
  }
}

void loop() {
  esp_task_wdt_reset();

  if (appState == ST_WIFI_PORTAL) {
    server.handleClient();
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
    if (WiFi.status() == WL_CONNECTED) {
      if (!mqttConnected) {
        if (connectMqtt()) {
          Serial.println("MQTT connected");
          publishEvent("home", NULL, -9999, NULL);
          if (!didFetchAllPages) {
            // Once per boot: ask the server for every page it knows about,
            // so navigating later can show cached content instantly instead
            // of a blank "Loading..." screen. Re-fetching this on every
            // sleep/wake reconnect isn't needed - the per-page cache stays
            // current via the normal navigate/toggle/edit round trips.
            publishEvent("get_all_pages", NULL, -9999, NULL);
            didFetchAllPages = true;
          }
        }
      } else {
        if (!mqtt.connected()) mqttConnected = false;
        else {
          mqtt.loop();
          flushPending();
          if (loadingPage && (now - pageRequestSentMs >= LOADING_RETRY_MS)) {
            pageRequestSentMs = now;
            publishEvent("navigate", NULL, -9999, currentPage.id);
          }
        }
      }
    }
  }

  // Draw is blocking (~450ms partial refresh). During it, a button press
  // would otherwise be lost because we can't scan. Detect via the keyEvents
  // ISR counter: if it advanced during the draw, immediately re-scan and
  // process so the tap that landed mid-refresh isn't swallowed.
  if (dirty) {
    const volatile int prevKeyEvents = keyEvents;
    requestDraw();
    if (keyEvents != prevKeyEvents) {
      scanMatrix(); scanMatrix();
      handleInput();
      commitStates();
    }
  }

  if (!active && appState != ST_WIFI_PORTAL) {
    Serial.println("going to sleep");
    Serial.flush();
    enterLightSleep();
    // After wake we reconnect
    startNetwork();
  }

  delay(2);
}
