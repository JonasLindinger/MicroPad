// AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
// MicroPad Home Assistant controller — Arduino entry point.
// This is the single Arduino sketch. All dependency-free logic lives in
// micropad_core.h/.cpp so it can be compiled and tested with a plain host C++
// toolchain (see scripts/test-firmware-host.sh and tests/firmware/).

#define DISABLE_DIAGNOSTIC_OUTPUT
#define MICROPAD_DEBUG 0
#include <GxEPD2_BW.h>
#include <Fonts/FreeMonoBold9pt7b.h>
#include <ArduinoJson.h>
#include <DNSServer.h>
#include <Preferences.h>
#include <PubSubClient.h>
#include <SPI.h>
#include <WebServer.h>
#include <WiFi.h>
#include <atomic>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <esp_heap_caps.h>
#include <esp_sleep.h>
#include <esp_system.h>
#include <esp_task_wdt.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include "micropad_core.h"
#include "protocol_contract.h"

// Bring the bounded copy helper into the sketch namespace. safeCopy() is a
// micropad template taking char[N]/const char* args — neither is a micropad
// type, so unqualified calls get no ADL and only an explicit using-declaration
// resolves them. Every other core call is either prefixed (micropad::) or
// found by ADL from a micropad:: argument type.
using micropad::safeCopy;

// The Arduino loop task runs setup/loop and the MQTT callback. With the
// 16-KiB MQTT buffer plus Page/Binding structures on that stack, the default
// 8-KiB loop-task stack overflows and panics the device (observed as PANIC /
// INT_WDT resets). Raise it to the documented 32 KiB.
size_t getArduinoLoopTaskStackSize(void) { return 32768; }

// Render adapters defined at the end of the sketch (see the Task 8 render
// block): requestDraw publishes on Core 1, renderTask draws on pinned Core 0.
void buildSnapshot(micropad::RenderSnapshot &out);
void renderTask(void *argument);

constexpr uint8_t PIN_EPD_CS = 8;
constexpr uint8_t PIN_EPD_DC = 40;
constexpr uint8_t PIN_EPD_RST = 41;
constexpr uint8_t PIN_EPD_BUSY = 42;
constexpr uint8_t PIN_EPD_SCK = 39;
constexpr uint8_t PIN_EPD_MOSI = 38;
constexpr uint8_t ROW_PINS[3] = {47, 48, 45};
constexpr uint8_t COL_PINS[4] = {21, 14, 13, 12};
constexpr uint8_t ENCODER_A_PIN = 10;
constexpr uint8_t ENCODER_B_PIN = 11;

// First MATRIX_KEY_COUNT entries map to KeyIds r0c0..r2c3 by (row*4+col).
micropad::DebouncedInput matrixDebounce[micropad::MATRIX_KEY_COUNT];
micropad::QuadratureDecoder encoderDecoder;

// ============================================================================
// Nonblocking captive portal. One DNSServer + WebServer pass runs per loop
// (portalTick), input scanning continues while the portal is active, Back
// cancels without touching NVS, and a valid POST saves through saveSettings()
// after the complete candidate validates. The AP password is random per boot
// (hardware entropy) and exists only in RAM; no credential is a source
// constant.
// ============================================================================

// Fixed sketch-side portal display state, copied by the display task into
// RenderSnapshot (Task 8).
struct PortalViewState {
  bool active;
  char ssid[33];
  char password[micropad::SETUP_PASSWORD_LENGTH + 1];
  char address[16];
  char instructions[65];
};

bool portalActive = false;
bool portalExitSaved = false;
bool portalRoutesRegistered = false;
uint32_t portalLastActivityMs = 0;
char setupPassword[micropad::SETUP_PASSWORD_LENGTH + 1] = {};
PortalViewState portalView = {};
DNSServer dnsServer;
WebServer webServer(80);

// 6-bit password alphabet: the & 0x3f index below needs exactly 64 entries.
constexpr char SETUP_ALPHABET[] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
static_assert(sizeof(SETUP_ALPHABET) - 1 == 64);

// Active-low matrix pins: every column pull-up means a released switch reads
// HIGH and a pressed switch pulls it LOW. Rows idle HIGH and are driven LOW one
// at a time while a single column is sampled, then restored HIGH before the
// next row is strobed so no address ghost leaks into an unattended switch.
void setupInputPins() {
  for (uint8_t row = 0; row < micropad::MATRIX_ROWS; ++row) {
    digitalWrite(ROW_PINS[row], HIGH);
    pinMode(ROW_PINS[row], OUTPUT);
  }
  for (uint8_t col = 0; col < micropad::MATRIX_COLS; ++col) {
    pinMode(COL_PINS[col], INPUT_PULLUP);
  }
  pinMode(ENCODER_A_PIN, INPUT_PULLUP);
  pinMode(ENCODER_B_PIN, INPUT_PULLUP);
  encoderDecoder.reset(static_cast<uint8_t>(
      (digitalRead(ENCODER_A_PIN) << 1) | digitalRead(ENCODER_B_PIN)));
}

bool readMatrixKey(uint8_t row, uint8_t col) {
  digitalWrite(ROW_PINS[row], LOW);
  const bool pressed = digitalRead(COL_PINS[col]) == LOW;
  digitalWrite(ROW_PINS[row], HIGH);
  return pressed;
}

// Seam where every debounced input leaves the scanners. Resolving the
// effective keymap and dispatching the resolved action (queue, optimistic
// state, portal, sleep) is wired by the event-queue task; scanners never call
// any other subsystem directly.
micropad::PadController pad;
bool catalogReady = false;

// Activity clocks for the warm-sleep gate (Task 11). lastActivityMs is
// refreshed by every emitted input so the idle timeout counts real inactivity;
// awakeStartedMs anchors the 3000 ms post-wake minimum-awake brake and is set
// at every wake (setup() seeds it at boot in Task 12).
uint32_t lastActivityMs = 0;
uint32_t awakeStartedMs = 0;

void requestDraw(micropad::DrawReason reason);

void emitInput(const micropad::InputEvent &input) {
  const size_t index = static_cast<size_t>(input.key);
  if (index >= micropad::KEY_COUNT) return;
  lastActivityMs = input.atMs;  // every emitted event is input activity
  const micropad::Binding &binding = pad.activeKeymap[index];
  micropad::Action action = micropad::resolveBinding(binding, input);
  if (!input.pressed) return;
  if (action == micropad::Action::Enter) action = pad.selectedItemAction();
  // While the setup portal is active every input refreshes its inactivity
  // clock; only Back acts on it, exiting without touching saved settings.
  if (portalActive) {
    portalLastActivityMs = input.atMs;
    if (action == micropad::Action::Back) cancelPortal();
    return;
  }
  // Settings entry through normal keymap resolution opens the setup portal.
  if (action == micropad::Action::Settings) {
    startPortal(input.atMs);
    return;
  }
  const micropad::ApplyResult result =
      pad.applyAction(action, binding, input.atMs);
  if (result.stateChanged) {
    requestDraw(micropad::DrawReason::StateChange);
  }
}

void scanMatrix(uint32_t nowMs) {
  micropad::scanMatrixPass(matrixDebounce, readMatrixKey, emitInput, nowMs);
}

void scanEncoder(uint32_t nowMs) {
  const uint8_t phase = static_cast<uint8_t>(
      (digitalRead(ENCODER_A_PIN) << 1) | digitalRead(ENCODER_B_PIN));
  micropad::scanEncoderPass(encoderDecoder, phase, emitInput, nowMs);
}

// Sleep-gate probe: the OR of every debounced held switch and a fresh
// active-low pass. Every row is restored HIGH before any press is acted on;
// resting encoder A/B levels are not held keys.
bool anyKeyHeld() {
  return micropad::anyMatrixKeyHeld(matrixDebounce, readMatrixKey);
}

// ============================================================================
// Persisted device settings (WiFi + MQTT), stored in Preferences/NVS. The
// dependency-free Settings type, neutral defaults and the validation rule live
// in micropad_core so host tests prove them without NVS; these two functions
// are the only NVS-touching seams and are pinned by static contract checks.
// ============================================================================

// Active settings. On first boot this is defaultSettings() (empty SSID ->
// first-boot portal); after loadSettings() it is populated from NVS when a
// complete, valid record exists.
micropad::Settings deviceSettings = micropad::defaultSettings();
bool mqttClientConfigured = false;

// Load and accept a stored settings record read-only. A value is used only
// when the version marker is current (cfg_ver == 1), every expected key is
// present, every bounded copy succeeds, and the complete result validates.
// The active settings are updated only on full success; NVS is closed on
// every path.
bool loadSettings() {
  Preferences preferences;
  if (!preferences.begin("micropad", true)) return false;

  micropad::Settings candidate = micropad::defaultSettings();
  const bool versionOk = preferences.getUChar("cfg_ver", 0) == 1;
  const bool keysPresent =
      preferences.isKey("wifi_ssid") && preferences.isKey("wifi_pass") &&
      preferences.isKey("mqtt_host") && preferences.isKey("mqtt_port") &&
      preferences.isKey("mqtt_user") && preferences.isKey("mqtt_pass") &&
      preferences.isKey("client_id");
  // Every bounded copy is safe by construction (getString is given the array
  // capacity and never overflows); presence is confirmed above, so a missing
  // or partial record never silently falls back to defaults.
  preferences.getString("wifi_ssid", candidate.wifiSsid,
                        sizeof(candidate.wifiSsid));
  preferences.getString("wifi_pass", candidate.wifiPassword,
                        sizeof(candidate.wifiPassword));
  preferences.getString("mqtt_host", candidate.mqttHost,
                        sizeof(candidate.mqttHost));
  candidate.mqttPort =
      preferences.getUShort("mqtt_port", candidate.mqttPort);
  preferences.getString("mqtt_user", candidate.mqttUser,
                        sizeof(candidate.mqttUser));
  preferences.getString("mqtt_pass", candidate.mqttPassword,
                        sizeof(candidate.mqttPassword));
  preferences.getString("client_id", candidate.clientId,
                        sizeof(candidate.clientId));
  preferences.end();

  if (!versionOk || !keysPresent || !micropad::validateSettings(candidate)) {
    return false;
  }
  deviceSettings = candidate;
  return true;
}

// Persist a validated settings record read-write. A complete candidate must
// validate before NVS is opened, credentials are never logged, and cfg_ver is
// written last as a commit marker so a partial write is never mistaken for a
// complete record. NVS is closed before returning.
bool putSettingsString(Preferences &preferences, const char *key,
                       const char *value) {
  return preferences.putString(key, value) == strlen(value);
}

bool saveSettings(const micropad::Settings &settings) {
  if (!micropad::validateSettings(settings)) return false;

  Preferences preferences;
  if (!preferences.begin("micropad", false)) return false;

  bool ok = true;
  ok &= putSettingsString(preferences, "wifi_ssid", settings.wifiSsid);
  ok &= putSettingsString(preferences, "wifi_pass", settings.wifiPassword);
  ok &= putSettingsString(preferences, "mqtt_host", settings.mqttHost);
  ok &= preferences.putUShort("mqtt_port", settings.mqttPort) > 0;
  ok &= putSettingsString(preferences, "mqtt_user", settings.mqttUser);
  ok &= putSettingsString(preferences, "mqtt_pass", settings.mqttPassword);
  ok &= putSettingsString(preferences, "client_id", settings.clientId);
  ok &= preferences.putUChar("cfg_ver", 1) > 0;  // commit marker: written last
  preferences.end();

  if (ok) {
    deviceSettings = settings;
    mqttClientConfigured = false;  // re-apply host/port on the next MQTT tick
  }
  return ok;
}

// ============================================================================
// Captive portal implementation (see the portal state block above). The
// WiFi/DNS/WebServer adapters live here in the sketch; the portal state
// machine, form->Settings validation and timeout logic are pinned by the
// static contract tests in tests/firmware/test_firmware_static.py.
// ============================================================================

// ============================================================================
// Dual-core render snapshot mailbox (Task 8). The dependency-free two-slot
// state machine lives in micropad_core (SnapshotMailbox, host-tested); every
// sketch call to it here is wrapped by portENTER_CRITICAL/portEXIT_CRITICAL so
// Core 1 publishes to the non-rendering slot while the Core 0 render task
// draws the newest immutable snapshot. Renderers wait on task notifications:
// delay(1) polling would break light sleep.
// ============================================================================

micropad::SnapshotMailbox snapshotMailbox;
portMUX_TYPE snapshotMux = portMUX_INITIALIZER_UNLOCKED;
TaskHandle_t renderTaskHandle = nullptr;
std::atomic<bool> renderBusy{false};
// Core 0 -> Core 1 display-recovery handshake (Task 12): recoverDisplay()
// sets this after a BUSY anomaly so the loop's renderRecoveryTick consumes it
// exactly once and requests one forced-full redraw through the normal mailbox
// publisher. Never an unconditional requestDraw from a loop path.
std::atomic<bool> recoveryDrawNeeded{false};
// Task 9 display state. pendingForceFull mirrors the forceFull flag of the
// newest published snapshot so the Core 0 spacing gate can skip the wait for
// forced-full starts (boot, portal transitions, recovery, partial limit).
// refreshPolicy owns the refresh-mode decision, the partial counter and the
// wrap-safe MIN_REFRESH_SPACING_MS spacing. The display object itself is
// touched only by Core 0 (initDisplay/drawSnapshot) and is the exclusive
// panel seam; the full native 128x296 panel window is established before
// setRotation(1) because the SSD1680 stores partial windows in native
// (unrotated) coordinates.
std::atomic<bool> pendingForceFull{false};
micropad::RefreshPolicy refreshPolicy;
GxEPD2_BW<GxEPD2_290_T94_V2, GxEPD2_290_T94_V2::HEIGHT> display(
    GxEPD2_290_T94_V2(PIN_EPD_CS, PIN_EPD_DC, PIN_EPD_RST, PIN_EPD_BUSY));
// Logical network state copied into every snapshot: 1 disconnected, 2
// connecting, 3 Wi-Fi only (no MQTT), 4 MQTT connected. Task 9 draws the
// corresponding status cell geometry.
enum NetworkState : uint8_t {
  NETWORK_DISCONNECTED = 1,
  NETWORK_CONNECTING = 2,
  NETWORK_WIFI_ONLY = 3,
  NETWORK_MQTT = 4
};
static_assert(NETWORK_DISCONNECTED == micropad::NETWORK_STATE_DISCONNECTED &&
                  NETWORK_CONNECTING == micropad::NETWORK_STATE_CONNECTING &&
                  NETWORK_WIFI_ONLY == micropad::NETWORK_STATE_WIFI_ONLY &&
                  NETWORK_MQTT == micropad::NETWORK_STATE_MQTT,
              "status cell geometry shares the network state numbering");

// Canonical MQTT wire topics and version come from firmware/protocol_contract.h
// (the single source of truth shared by the config generator, /api/meta, the
// browser, and this sketch). Subscribing and publishing read the mp::TOPIC_*
// constants so the device always speaks the contract binding — never a
// duplicated literal.
WiFiClient mqttNetworkClient;
PubSubClient mqttClient(mqttNetworkClient);
uint32_t wifiLastAttemptMs = 0;
bool wifiWasConnected = false;
// Seed one full retry interval in the past so the first attempt is immediate;
// later failures remain throttled by MQTT_RETRY_MS.
uint32_t mqttNextAttemptMs =
    static_cast<uint32_t>(0U - micropad::MQTT_RETRY_MS);
bool firstMqttConnectionThisBoot = false;

uint8_t currentNetworkState() {
  if (portalActive) return NETWORK_CONNECTING;
  if (mqttClient.connected()) return NETWORK_MQTT;
  return WiFi.isConnected() ? NETWORK_WIFI_ONLY : NETWORK_DISCONNECTED;
}

// Called from portal transitions, MQTT commits, and (later tasks) power edges
// and recovery. Maps boot/portal-transition/recovery/partial-limit reasons to
// a forced full refresh, obtains a write slot under the mutex, fills the
// snapshot outside it, publishes under the mutex, and wakes the Core 0 render
// task. Bursts of requests during a refresh replace the non-rendering slot so
// only the newest generation survives.
void requestDraw(micropad::DrawReason reason) {
  const bool forceFull = micropad::refreshMustBeFull(reason);
  int8_t slot = -1;
  portENTER_CRITICAL(&snapshotMux);
  slot = snapshotMailbox.beginWrite();
  portEXIT_CRITICAL(&snapshotMux);
  if (slot < 0) return;  // both slots busy: nothing can be published
  micropad::RenderSnapshot &out = snapshotMailbox.writable(slot);
  out.forceFull = forceFull;
  buildSnapshot(out);
  portENTER_CRITICAL(&snapshotMux);
  snapshotMailbox.publish(slot);
  portEXIT_CRITICAL(&snapshotMux);
  pendingForceFull.store(forceFull, std::memory_order_release);
  xTaskNotifyGive(renderTaskHandle);
}

// 96-bit setup password from hardware entropy. Each random byte is masked to
// 6 bits and indexes the 64-character alphabet: no modulo bias, and never a
// timestamp, MAC address, or seeded PRNG substitute.
void generateSetupPassword(
    char (&out)[micropad::SETUP_PASSWORD_LENGTH + 1]) {
  uint8_t randomBytes[micropad::SETUP_PASSWORD_LENGTH];
  esp_fill_random(randomBytes, sizeof(randomBytes));
  for (size_t i = 0; i < micropad::SETUP_PASSWORD_LENGTH; ++i) {
    out[i] = SETUP_ALPHABET[randomBytes[i] & 0x3f];
  }
  out[micropad::SETUP_PASSWORD_LENGTH] = '\0';
}

// Escape the five HTML-special characters before reflecting any user field.
void appendEscaped(String &out, const char *text) {
  if (text == nullptr) return;
  for (const char *p = text; *p != '\0'; ++p) {
    if (*p == '&') out += "&amp;";
    else if (*p == '<') out += "&lt;";
    else if (*p == '>') out += "&gt;";
    else if (*p == '"') out += "&quot;";
    else if (*p == '\'') out += "&#39;";
    else out += *p;
  }
}

// Minimal setup form. Every user-supplied field is reflected escaped; neither
// password is ever pre-filled; the generated AP password is displayed once so
// the phone can join the AP.
String portalPage() {
  String page = F("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                  "<meta name='viewport' content='width=device-width, "
                  "initial-scale=1'><title>MicroPad Setup</title></head>"
                  "<body><h1>MicroPad Setup</h1>"
                  "<p>Join Wi-Fi <b>MicroPad-Setup</b> using this password: "
                  "<code>");
  page += setupPassword;
  page += F("</code></p>"
            "<form method='post' action='/'>"
            "<p><label>Wi-Fi SSID<br><input name='ssid' value='");
  appendEscaped(page, deviceSettings.wifiSsid);
  page += F("'></label></p>"
            "<p><label>Wi-Fi password<br>"
            "<input type='password' name='wifi_pass'></label></p>"
            "<p><label>MQTT host<br><input name='mqtt_host' value='");
  appendEscaped(page, deviceSettings.mqttHost);
  page += F("'></label></p>"
            "<p><label>MQTT port<br><input name='mqtt_port' value='");
  page += String(deviceSettings.mqttPort);
  page += F("'></label></p>"
            "<p><label>MQTT user<br><input name='mqtt_user' value='");
  appendEscaped(page, deviceSettings.mqttUser);
  page += F("'></label></p>"
            "<p><label>MQTT password<br>"
            "<input type='password' name='mqtt_pass'></label></p>"
            "<p><label>Client ID<br><input name='client_id' value='");
  appendEscaped(page, deviceSettings.clientId);
  page += F("'></label></p>"
            "<p><button type='submit'>Save</button></p>"
            "</form></body></html>");
  return page;
}

const char *portalSuccessPage() {
  return "<!DOCTYPE html><html><head><meta charset='utf-8'>"
         "<title>MicroPad Setup</title></head><body>"
         "<h1>Saved</h1>"
         "<p>Wi-Fi and MQTT settings were stored on the device. The setup "
         "portal is closing and the device is reconnecting.</p></body></html>";
}

void handlePortalGet() {
  portalLastActivityMs = millis();
  webServer.sendHeader("Cache-Control", "no-store");
  webServer.send(200, "text/html", portalPage());
}

// Reject missing or oversized fields and a non-decimal/out-of-range port with
// HTTP 400. Every accepted field is bounded-copied into the candidate and the
// complete candidate validates before any NVS call.
void handlePortalPost() {
  portalLastActivityMs = millis();

  if (!webServer.hasArg("ssid") || !webServer.hasArg("wifi_pass") ||
      !webServer.hasArg("mqtt_host") || !webServer.hasArg("mqtt_port") ||
      !webServer.hasArg("mqtt_user") || !webServer.hasArg("mqtt_pass") ||
      !webServer.hasArg("client_id")) {
    webServer.send(400, "text/plain", "missing field");
    return;
  }

  micropad::Settings candidate = micropad::defaultSettings();
  bool fieldsOk = true;
  fieldsOk &= safeCopy(candidate.wifiSsid, webServer.arg("ssid").c_str());
  fieldsOk &= safeCopy(candidate.wifiPassword,
                       webServer.arg("wifi_pass").c_str());
  fieldsOk &= safeCopy(candidate.mqttHost, webServer.arg("mqtt_host").c_str());
  fieldsOk &= safeCopy(candidate.mqttUser, webServer.arg("mqtt_user").c_str());
  fieldsOk &= safeCopy(candidate.mqttPassword,
                       webServer.arg("mqtt_pass").c_str());
  fieldsOk &= safeCopy(candidate.clientId, webServer.arg("client_id").c_str());

  const String portText = webServer.arg("mqtt_port");
  bool decimalPort = portText.length() > 0;
  for (size_t i = 0; decimalPort && i < portText.length(); ++i) {
    if (portText[i] < '0' || portText[i] > '9') decimalPort = false;
  }
  const unsigned long portValue =
      decimalPort ? strtoul(portText.c_str(), nullptr, 10) : 0UL;

  if (!fieldsOk) {
    webServer.send(400, "text/plain", "field too long");
    return;
  }
  if (!decimalPort || portValue < 1 || portValue > 65535) {
    webServer.send(400, "text/plain", "invalid port");
    return;
  }
  candidate.mqttPort = static_cast<uint16_t>(portValue);

  // The complete candidate validates before NVS is opened (see F5).
  if (!micropad::validateSettings(candidate)) {
    webServer.send(400, "text/plain", "invalid settings");
    return;
  }
  if (!saveSettings(candidate)) {
    webServer.send(500, "text/plain", "save failed");
    return;
  }
  webServer.send(200, "text/html", portalSuccessPage());
  portalExitSaved = true;  // portalTick tears the portal down next pass
}

#if MICROPAD_DEBUG
// Diagnostic endpoint, compiled out of release builds (MICROPAD_DEBUG == 0).
// Exposes only the queue overflow counter: never credentials or payloads.
void handlePortalDebug() {
  webServer.send(200, "text/plain",
                 String("event_queue_overflow=") + pad.queue.overflowCount());
}
#endif

// One-pass portal service. A successful POST was already fully responded to,
// so the exit flag tears the portal down here, after the request handler
// returned; otherwise one DNS and one HTTP pass run per call, input scanning
// keeps running, and the device restarts after PORTAL_IDLE_MS of inactivity.
void portalTick(uint32_t nowMs) {
  if (portalExitSaved) {
    stopPortal(true);
    return;
  }
  if (!portalActive) return;
  dnsServer.processNextRequest();
  webServer.handleClient();
  if (static_cast<uint32_t>(nowMs - portalLastActivityMs) >=
      micropad::PORTAL_IDLE_MS) {
    ESP.restart();
  }
}

// Start the AP, wildcard DNS and WebServer once, publish the portal view
// state, and request a forced full refresh. Never waits for clients.
void startPortal(uint32_t nowMs) {
  if (portalActive) return;
  generateSetupPassword(setupPassword);
  portalActive = true;
  portalExitSaved = false;
  portalLastActivityMs = nowMs;
  portalView.active = true;
  safeCopy(portalView.ssid, "MicroPad-Setup");
  safeCopy(portalView.password, setupPassword);
  safeCopy(portalView.address, "192.168.4.1");
  safeCopy(portalView.instructions, "Open http://192.168.4.1");

  WiFi.mode(WIFI_AP);
  WiFi.softAP("MicroPad-Setup", setupPassword);
  dnsServer.setErrorReplyCode(DNSReplyCode::NoError);
  dnsServer.start(53, "*", WiFi.softAPIP());
  if (!portalRoutesRegistered) {
    webServer.on("/", HTTP_GET, handlePortalGet);
    webServer.on("/", HTTP_POST, handlePortalPost);
#if MICROPAD_DEBUG
    webServer.on("/debug", HTTP_GET, handlePortalDebug);
#endif
    portalRoutesRegistered = true;
  }
  webServer.begin();
  requestDraw(micropad::DrawReason::PortalEnter);
}

// Stop the server/DNS/AP, return Wi-Fi to station mode, force a full refresh,
// and start reconnecting only when settings were saved. NVS is never written
// here: cancelPortal() -> stopPortal(false) leaves Preferences untouched.
void stopPortal(bool saved) {
  if (!portalActive) return;
  portalActive = false;
  portalExitSaved = false;
  webServer.stop();
  dnsServer.stop();
  WiFi.softAPdisconnect();
  WiFi.mode(WIFI_STA);
  portalView.active = false;
  requestDraw(micropad::DrawReason::PortalExit);
  if (saved) {
    // Reconnect starts here; the throttled Wi-Fi state machine (Task 7)
    // continues retries at WIFI_RETRY_MS from station mode. Recording the
    // attempt here resets the retry schedule so this begin() is not doubled.
    wifiLastAttemptMs = millis();
    WiFi.begin(deviceSettings.wifiSsid, deviceSettings.wifiPassword);
  }
}

void cancelPortal() { stopPortal(false); }

// ============================================================================
// Independent WiFi/MQTT state machines (Task 7). wifiTick() and mqttTick()
// are nonblocking: each performs at most one network call per WIFI_RETRY_MS /
// MQTT_RETRY_MS and never waits for connection state. All cache/keymap parse
// validation and the page/keymap commit decisions live in the dependency-free
// core (micropad_core.*); the ArduinoJson text parsing and the thin
// WiFi/PubSubClient adapters live here.
// ============================================================================

// Owned by powerTick(): reflects the debounced, stable USB host state (Task
// 10). False until a Connected edge stabilizes; a charger without data lines
// keeps this false since isPowered() probes the CDC data session only.
// Published retained on every MQTT connection by publishInitialRequests().
bool stableUsbHost = false;

// One throttled Wi-Fi pass: begin() at most once per WIFI_RETRY_MS while
// disconnected (WiFi.begin is asynchronous; nothing waits for WL_CONNECTED).
// A freshly lost link and a portal-saved reconnect reset the schedule.
void wifiTick(uint32_t nowMs) {
  if (portalActive) return;
  if (WiFi.isConnected()) {
    wifiWasConnected = true;
    return;
  }
  if (wifiWasConnected) {
    wifiWasConnected = false;
    wifiLastAttemptMs = 0;
  }
  if (deviceSettings.wifiSsid[0] == '\0') return;
  if (static_cast<uint32_t>(nowMs - wifiLastAttemptMs) <
      micropad::WIFI_RETRY_MS) {
    return;
  }
  wifiLastAttemptMs = nowMs;
  WiFi.begin(deviceSettings.wifiSsid, deviceSettings.wifiPassword);
}

// Queue a reserved system event addressed to a page (e.g. GetAllPages on the
// first connection). The event rides the regular outgoing queue so ordering
// with user events is preserved.
bool queueSystemEvent(micropad::Action action, const char *pageId) {
  micropad::Event event{};
  event.action = action;
  safeCopy(event.pageId, pageId);
  return pad.queue.push(event);
}

// Queue a navigate request for the given page: the backend republishes the
// authoritative current page and keymap for it (quiet-period resync and
// every reconnect).
bool queueNavigateRequest(const char *pageId) {
  micropad::Event event{};
  event.action = micropad::Action::Navigate;
  safeCopy(event.targetPage, pageId);
  return pad.queue.push(event);
}

void publishPowerStateRetained(bool usbHost) {
  if (!mqttClient.connected()) return;
  JsonDocument doc;
  doc["usb_host"] = usbHost;
  char buffer[32];
  const size_t written = serializeJson(doc, buffer, sizeof(buffer));
  if (written == 0 || written >= sizeof(buffer)) return;  // never malformed
  mqttClient.publish(mp::TOPIC_POWER, buffer, true);
}

// Exactly one get_all_pages request per boot; every reconnect (and every
// quiet-period resync) asks for the current authoritative page instead. The
// retained subscriptions received on connect normally satisfy both.
void publishInitialRequests() {
  const char *requestedPage =
      pad.state.pageId[0] == '\0' ? "home" : pad.state.pageId;
  if (!firstMqttConnectionThisBoot) {
    queueSystemEvent(micropad::Action::GetAllPages, requestedPage);
    firstMqttConnectionThisBoot = true;
  } else {
    queueNavigateRequest(requestedPage);
  }
  publishPowerStateRetained(stableUsbHost);
}

// ============================================================================
// Retained-cache parsing (micropad/pages/all, micropad/page/current,
// micropad/keymap). Every parse validates completely before any live field is
// assigned; the commit/swap decisions themselves are dependency-free core
// functions proven on the host.
// ============================================================================

namespace {

const char *jsonString(JsonVariantConst value) {
  return value.is<const char *>() ? value.as<const char *>() : nullptr;
}

// Copy an optional string field: absent (null) keeps the destination empty;
// a wrong type or an overflow rejects the whole payload.
template <size_t N>
bool copyOptionalString(JsonVariantConst value, char (&dst)[N]) {
  if (value.isNull()) {
    dst[0] = '\0';
    return true;
  }
  const char *text = jsonString(value);
  if (text == nullptr) return false;
  return safeCopy(dst, text);
}

}  // namespace

bool parseBinding(JsonVariantConst value, micropad::Binding &out) {
  if (!value.is<JsonObjectConst>()) return false;
  JsonObjectConst obj = value.as<JsonObjectConst>();
  const char *actionText = jsonString(obj["action"]);
  if (actionText == nullptr) return false;
  micropad::Action action;
  if (!micropad::parseAction(actionText, action)) return false;
  out.action = action;
  if (!copyOptionalString(obj["entity"], out.entity)) return false;
  if (!copyOptionalString(obj["target_page"], out.targetPage)) return false;
  return true;
}

bool parseItem(JsonVariantConst value, micropad::Item &out) {
  if (!value.is<JsonObjectConst>()) return false;
  JsonObjectConst obj = value.as<JsonObjectConst>();
  const char *name = jsonString(obj["name"]);
  const char *typeText = jsonString(obj["type"]);
  if (name == nullptr || typeText == nullptr) return false;
  if (!safeCopy(out.name, name)) return false;
  if (!micropad::parseItemType(typeText, out.type)) return false;
  if (!copyOptionalString(obj["entity"], out.entity)) return false;
  if (!copyOptionalString(obj["state"], out.state)) return false;
  if (!copyOptionalString(obj["unit"], out.unit)) return false;
  if (!copyOptionalString(obj["target_page"], out.targetPage)) return false;
  // Numeric/edit fields are optional and defaulted so retained payloads from
  // older generators (name/type/entity/state only) stay loadable; when any is
  // present it must be numeric (or boolean for editable) and the resulting
  // combination must remain finite and bounded (min <= max, step > 0).
  out.value = obj["value"].is<float>() ? obj["value"].as<float>() : 0.0f;
  out.min = obj["min"].is<float>() ? obj["min"].as<float>() : 0.0f;
  out.max = obj["max"].is<float>() ? obj["max"].as<float>() : 100.0f;
  out.step = obj["step"].is<float>() ? obj["step"].as<float>() : 1.0f;
  out.editable =
      obj["editable"].is<bool>() ? obj["editable"].as<bool>() : false;
  if (!std::isfinite(out.value) || !std::isfinite(out.min) ||
      !std::isfinite(out.max) || !std::isfinite(out.step)) {
    return false;
  }
  if (out.min > out.max || out.step <= 0.0f) return false;
  return true;
}

bool parsePage(JsonVariantConst value, micropad::Page &out) {
  if (!value.is<JsonObjectConst>()) return false;
  JsonObjectConst obj = value.as<JsonObjectConst>();
  const char *pageId = jsonString(obj["page_id"]);
  const char *title = jsonString(obj["title"]);
  if (pageId == nullptr || pageId[0] == '\0') return false;
  if (title == nullptr || title[0] == '\0') return false;
  if (!safeCopy(out.pageId, pageId)) return false;
  if (!safeCopy(out.title, title)) return false;
  if (!copyOptionalString(obj["parent"], out.parent)) return false;
  const JsonVariantConst itemsValue = obj["items"];
  if (!itemsValue.is<JsonArrayConst>()) return false;
  JsonArrayConst items = itemsValue.as<JsonArrayConst>();
  if (items.size() > micropad::MAX_ITEMS_PER_PAGE) return false;
  uint8_t count = 0;
  for (JsonVariantConst itemValue : items) {
    if (!parseItem(itemValue, out.items[count])) return false;
    ++count;
  }
  out.itemCount = count;
  return true;
}

bool parseCatalog(JsonVariantConst root, micropad::Catalog &out) {
  // Canonical backend shape: {"pages": [...]}; the clean-room spec also
  // documents a bare array of page payloads. Accept exactly these two shapes.
  const JsonVariantConst pagesValue =
      root.is<JsonArrayConst>() ? root : root["pages"];
  if (!pagesValue.is<JsonArrayConst>()) return false;
  JsonArrayConst pages = pagesValue.as<JsonArrayConst>();
  if (pages.size() > micropad::MAX_PAGES) return false;
  uint8_t count = 0;
  for (JsonVariantConst pageValue : pages) {
    if (!parsePage(pageValue, out.pages[count])) return false;
    for (uint8_t i = 0; i < count; ++i) {
      if (std::strcmp(out.pages[i].pageId, out.pages[count].pageId) == 0) {
        return false;  // unique nonempty page ids only
      }
    }
    ++count;
  }
  out.pageCount = count;
  return true;
}

bool parseCurrentPage(JsonVariantConst root, micropad::Page &out) {
  return parsePage(root, out);
}

bool parseEffectiveKeymap(JsonVariantConst root,
                          micropad::Binding (&out)[micropad::KEY_COUNT]) {
  micropad::Binding staging[micropad::KEY_COUNT]{};
  if (root.is<JsonObjectConst>()) {
    // Canonical backend shape: {key_id: {action, entity, target_page}} with
    // exactly the fourteen physical keys (a fifteenth key is rejected).
    JsonObjectConst obj = root.as<JsonObjectConst>();
    if (obj.size() != micropad::KEY_COUNT) return false;
    for (uint8_t i = 0; i < micropad::KEY_COUNT; ++i) {
      const char *keyName = micropad::keyIdName(static_cast<micropad::KeyId>(i));
      const JsonVariantConst bindingValue = obj[keyName];
      if (bindingValue.isNull()) return false;  // missing key id
      if (!parseBinding(bindingValue, staging[i])) return false;
    }
  } else if (root.is<JsonArrayConst>()) {
    // Clean-room spec shape: an array of exactly 14 objects carrying key_id.
    JsonArrayConst entries = root.as<JsonArrayConst>();
    if (entries.size() != micropad::KEY_COUNT) return false;
    bool seen[micropad::KEY_COUNT] = {};
    for (JsonVariantConst entry : entries) {
      if (!entry.is<JsonObjectConst>()) return false;
      const char *keyId = jsonString(entry["key_id"]);
      if (keyId == nullptr) return false;
      micropad::KeyId parsedKey;
      if (!micropad::parseKeyId(keyId, parsedKey)) return false;
      const uint8_t index = static_cast<uint8_t>(parsedKey);
      if (seen[index]) return false;  // duplicate key ids rejected
      seen[index] = true;
      if (!parseBinding(entry, staging[index])) return false;
    }
    for (uint8_t i = 0; i < micropad::KEY_COUNT; ++i) {
      if (!seen[i]) return false;  // every key must be present
    }
  } else {
    return false;
  }
  // All 14 entries validated: the complete staging array is committed in one
  // operation by the caller (replaceKeymap); nothing below can fail.
  std::memcpy(out, staging, sizeof(staging));
  return true;
}

// Bounded one-pass MQTT callback (defined before mqttTick so setCallback can
// take its address without a forward declaration): an oversized payload is
// rejected before the copy, the fixed MQTT_BUFFER_BYTES + 1 buffer is copied
// and NUL-terminated exactly once, the payload is deserialized exactly once,
// and the parsed root is dispatched by exact topic. Live cache/keymap fields
// change only after a complete parse succeeds.
void onMqttMessage(char *topic, uint8_t *payload, unsigned int length) {
  if (length > micropad::MQTT_BUFFER_BYTES) return;
  uint8_t mqttPayload[micropad::MQTT_BUFFER_BYTES + 1];
  memcpy(mqttPayload, payload, length);
  mqttPayload[length] = '\0';
  JsonDocument doc;
  if (deserializeJson(doc, mqttPayload, length) != DeserializationError::Ok) {
    return;
  }
  JsonVariantConst root = doc.as<JsonVariantConst>();
  if (std::strcmp(topic, mp::TOPIC_PAGES) == 0) {
    // Stage the full catalog off the live cache and swap atomically: the
    // staging allocation is PSRAM-capability aware and is freed on every
    // path; the live cache is replaced only by a complete, validated parse.
    micropad::Catalog *staging = static_cast<micropad::Catalog *>(
        psramFound()
            ? heap_caps_calloc(1, sizeof(micropad::Catalog),
                               MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)
            : calloc(1, sizeof(micropad::Catalog)));
    if (staging == nullptr) return;
    if (parseCatalog(root, *staging)) {
      pad.catalog = *staging;
      catalogReady = true;
      micropad::Page *displayedPage = pad.currentPage();
      if (displayedPage != nullptr) {
        micropad::normalizeSelection(displayedPage, pad.state);
        requestDraw(micropad::DrawReason::Boot);
      }
    }
    free(staging);
    return;
  }
  if (std::strcmp(topic, mp::TOPIC_CURRENT_PAGE) == 0) {
    micropad::Page page{};
    if (parseCurrentPage(root, page)) {
      const bool displayedPage =
          catalogReady && std::strcmp(pad.state.pageId, page.pageId) == 0;
      const micropad::Page *cached =
          micropad::findPage(pad.catalog, page.pageId);
      const bool contentChanged =
          cached == nullptr || !micropad::pageContentEqual(*cached, page);
      if (commitCurrentPage(pad, page, millis()) && displayedPage &&
          contentChanged) {
        requestDraw(micropad::DrawReason::StateChange);
      }
    }
    return;
  }
  if (std::strcmp(topic, mp::TOPIC_KEYMAP) == 0) {
    micropad::Binding staging[micropad::KEY_COUNT]{};
    if (parseEffectiveKeymap(root, staging)) {
      replaceKeymap(pad.activeKeymap, staging);
    }
    return;
  }
}

// One throttled MQTT pass (after onMqttMessage so the callback address needs
// no forward declaration): connect at most once per MQTT_RETRY_MS while Wi-Fi
// is up, never looping around connect(). The buffer is widened to the full
// 16 KiB contract before any connect, and the MAC-derived client id is
// snprintf'd into the bounded buffer with an explicit truncation rejection.
void mqttTick(uint32_t nowMs) {
  if (portalActive) return;
  if (WiFi.status() != WL_CONNECTED) return;
  if (!mqttClientConfigured) {
    mqttClient.setBufferSize(micropad::MQTT_BUFFER_BYTES);
    mqttClient.setSocketTimeout(1);
    mqttNetworkClient.setConnectionTimeout(100);
    mqttClient.setServer(deviceSettings.mqttHost, deviceSettings.mqttPort);
    mqttClient.setCallback(onMqttMessage);
    mqttClientConfigured = true;
  }
  if (mqttClient.connected()) return;
  if (static_cast<uint32_t>(nowMs - mqttNextAttemptMs) <
      micropad::MQTT_RETRY_MS) {
    return;
  }
  char clientIdBuffer[micropad::CLIENT_ID_CAP];
  if (deviceSettings.clientId[0] != '\0') {
    if (!safeCopy(clientIdBuffer, deviceSettings.clientId)) return;
  } else {
    const uint64_t mac = ESP.getEfuseMac();
    const int written = snprintf(clientIdBuffer, sizeof(clientIdBuffer),
                                 "micropad-%012llx",
                                 static_cast<unsigned long long>(mac));
    if (written < 0 ||
        static_cast<size_t>(written) >= sizeof(clientIdBuffer)) {
      return;  // truncated MAC-derived id: never connect under a bad id
    }
  }
  mqttNextAttemptMs = nowMs;
  if (!mqttClient.connect(clientIdBuffer, deviceSettings.mqttUser,
                          deviceSettings.mqttPassword)) {
    return;
  }
  mqttClient.subscribe(mp::TOPIC_PAGES);
  mqttClient.subscribe(mp::TOPIC_CURRENT_PAGE);
  mqttClient.subscribe(mp::TOPIC_KEYMAP);
  publishInitialRequests();
}

// ============================================================================
// Outbound events and quiet-period resync.
// ============================================================================

// One compact event object per the backend contract: action is always
// present; entity, value, page_id, and target_page are included only when
// relevant/nonempty. Serialization is bounded by EVENT_BUFFER_BYTES; an
// overflow is rejected without producing malformed data.
bool serializeEvent(const micropad::Event &event, char *buffer,
                    size_t capacity) {
  const char *name = micropad::actionName(event.action);
  if (name == nullptr || capacity == 0) return false;
  JsonDocument doc;
  JsonObject obj = doc.to<JsonObject>();
  obj["action"] = name;
  if (event.entity[0] != '\0') obj["entity"] = event.entity;
  if (event.action == micropad::Action::Edit) obj["value"] = event.value;
  if (event.pageId[0] != '\0') obj["page_id"] = event.pageId;
  if (event.targetPage[0] != '\0') obj["target_page"] = event.targetPage;
  if (measureJson(doc) > capacity) return false;
  const size_t written = serializeJson(doc, buffer, capacity);
  return written > 0 && written < capacity;
}

// Pop and publish at most MAX_FLUSH_PER_LOOP events while connected. A failed
// publish (or an unserializable event) restores the just-popped event at the
// queue front via pushFront() and stops this pass, leaving every later event
// in its original order. Events are never retained.
void flushEventQueue() {
  if (portalActive) return;  // all MQTT outbound activity stops at portal entry
  if (!mqttClient.connected()) return;
  for (size_t count = 0; count < micropad::MAX_FLUSH_PER_LOOP; ++count) {
    micropad::Event event;
    if (!pad.queue.pop(event)) break;
    char buffer[micropad::EVENT_BUFFER_BYTES];
    if (!serializeEvent(event, buffer, sizeof(buffer))) {
      pad.queue.pushFront(event);
      break;
    }
    if (!mqttClient.publish(mp::TOPIC_EVENT, buffer, false)) {
      pad.queue.pushFront(event);
      break;
    }
  }
}

// Quiet-period authoritative resync: after RESYNC_SETTLE_MS without input the
// current page (or "home" before the cache is populated) is requested once.
// The flag clears only when the request was actually queued, and the event
// flush above has already preserved user-event ordering.
void resyncTick(uint32_t nowMs) {
  if (portalActive) return;
  if (!mqttClient.connected()) return;
  if (!pad.resyncDue(nowMs)) return;
  const char *requestedPage =
      pad.state.pageId[0] == '\0' ? "home" : pad.state.pageId;
  if (queueNavigateRequest(requestedPage)) {
    pad.state.resyncPending = false;
  }
}

// ============================================================================
// Debounce-guarded USB host detection and power state (Task 10). The power
// model is the data lines only: isPowered() means an active CDC data
// host/session (Serial convertible to bool under the CDC-on-boot guard),
// never a VBUS/charger GPIO guess — a charger without data lines therefore
// reports disconnected and the pad keeps running on battery. PowerDebouncer
// (dependency-free, host-tested) turns raw probes into stable 1500 ms edges;
// powerTick() consumes one raw probe per USB_SAMPLE_MS and calls
// requestDraw() plus the retained micropad/power publish only on debounced
// edges — never unconditionally from a loop path and never for raw samples.
// publishPowerStateRetained() additionally republishes the current stable
// state after every successful MQTT connection (see publishInitialRequests).
// ============================================================================

micropad::PowerDebouncer powerDebouncer;
uint32_t lastUsbSampleMs = 0;

// Active CDC data host/session, never inferred charge-only power.
bool isPowered() {
#if defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
  return static_cast<bool>(Serial);
#else
  return false;
#endif
}

// setTxTimeoutMs is a HWCDC-only method: the ARDUINO_USB_MODE == 1 arm keeps
// TinyUSB and CDC-disabled builds from ever compiling the unavailable call.
void configureCdc() {
#if defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT && \
    defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 1
  Serial.setTxTimeoutMs(0);
#endif
}

// One debounced power pass per loop: at most one raw probe every
// USB_SAMPLE_MS, and a snapshot request plus retained publish only when a raw
// probe stabilizes into a Connected/Disconnected edge. PowerEdge::None
// produces no side effect; stableUsbHost is updated on the edge so the next
// snapshot renders the power icon (buildSnapshot copies it into usbHost).
void powerTick(uint32_t nowMs) {
  if (static_cast<uint32_t>(nowMs - lastUsbSampleMs) <
      micropad::USB_SAMPLE_MS) {
    return;
  }
  lastUsbSampleMs = nowMs;
  const micropad::PowerEdge edge = powerDebouncer.sample(isPowered(), nowMs);
  if (edge == micropad::PowerEdge::Connected) {
    stableUsbHost = true;
    requestDraw(micropad::DrawReason::PowerEdge);
    publishPowerStateRetained(true);
  } else if (edge == micropad::PowerEdge::Disconnected) {
    stableUsbHost = false;
    requestDraw(micropad::DrawReason::PowerEdge);
    publishPowerStateRetained(false);
  }
}

// ============================================================================
// Warm light sleep entry and recovery (Task 11). Sleep happens only when NOT
// USB-powered, gated by sleepGateReady() -> canSleep() (60000 ms idle,
// 3000 ms since the last wake, no held key, no active or pending render), and
// enterLightSleep() repeats the independent power guard as the second check.
// Before sleep the matrix rows are driven LOW and every column plus encoder
// A/B is INPUT_PULLUP so a pressed key pulls a wake line LOW (ext1 any-low
// wake on GPIO21/14/13/12/10/11). The render task is removed from the task
// watchdog and suspended, the Wi-Fi station and MQTT objects stay intact with
// modem sleep enabled, and sleep duration is measured with esp_timer so it is
// independent of paused or moved millis(). After wake the renderer is resumed
// and re-watched only if it was watched before, the rows return to HIGH, a
// sleep longer than the 15 s MQTT keepalive invalidates the likely-stale
// socket and schedules an immediate reconnect, debounce is re-primed so the
// immediate scan delivers the wake press exactly once, the 3000 ms wake-loop
// brake starts, and the waking input is processed before this function
// returns. Nothing on this path prints to Serial.
// ============================================================================

// The loop task is the task-watchdog subscriber (unlike v6's backtrace-style
// comments, here the render task is deliberately NOT subscribed so long GxEPD2
// panel busy-waits on Core 0 cannot trip the TWDT; the loop task feeds the
// watchdog every pass and leaves/re-arms it around light sleep).
constexpr uint64_t MQTT_KEEPALIVE_US = 15000000ULL;  // 15 s keepalive

// First independent power guard: refuses sleep while USB powered, then asks
// the dependency-free predicate with the held-key, render-busy (acquire), and
// mailbox-pending state, plus the wrap-safe idle and minimum-awake clocks.
bool sleepGateReady(uint32_t nowMs) {
  if (isPowered()) return false;
  return micropad::canSleep(false, anyKeyHeld(),
                            renderBusy.load(std::memory_order_acquire),
                            snapshotMailbox.hasPending(), nowMs,
                            lastActivityMs, awakeStartedMs);
}

// Configure the wake-capable active-low inputs and the ext1 any-low wake
// source: every column and encoder A/B pulls up, every matrix row is driven
// LOW so a pressed key pulls its column LOW and wakes the device.
void prepareWakeInputs() {
  for (uint8_t row = 0; row < micropad::MATRIX_ROWS; ++row) {
    digitalWrite(ROW_PINS[row], LOW);
  }
  for (uint8_t col = 0; col < micropad::MATRIX_COLS; ++col) {
    pinMode(COL_PINS[col], INPUT_PULLUP);
  }
  pinMode(ENCODER_A_PIN, INPUT_PULLUP);
  pinMode(ENCODER_B_PIN, INPUT_PULLUP);
  // Proven v6 wake mechanism: per-pin GPIO level wakeup, NOT ext1. ext1 leaves
  // the S3 pins in the RTC/low-power path, and after wake the normal GPIO
  // output registers for the matrix rows were not restored — one pressed key
  // was read in all three rows (observed live as r0c3+r1c3+r2c3 together).
  esp_sleep_enable_gpio_wakeup();
  for (uint8_t col = 0; col < micropad::MATRIX_COLS; ++col) {
    gpio_wakeup_enable(static_cast<gpio_num_t>(COL_PINS[col]),
                       GPIO_INTR_LOW_LEVEL);
  }
  // Encoder wake on level change from the current state (same as v6).
  gpio_wakeup_enable(static_cast<gpio_num_t>(ENCODER_A_PIN),
                     digitalRead(ENCODER_A_PIN) == HIGH ? GPIO_INTR_LOW_LEVEL
                                                        : GPIO_INTR_HIGH_LEVEL);
  gpio_wakeup_enable(static_cast<gpio_num_t>(ENCODER_B_PIN),
                     digitalRead(ENCODER_B_PIN) == HIGH ? GPIO_INTR_LOW_LEVEL
                                                        : GPIO_INTR_HIGH_LEVEL);
}

// Second, independent power guard, then timed light sleep. The render task is
// watchdog-removed and suspended before sleep; Wi-Fi association and the MQTT
// objects stay intact with modem sleep enabled. No display, Serial, MQTT
// publish, or Preferences call occurs between the guard and the sleep start.
void enterLightSleep(uint32_t nowMs) {
  if (isPowered()) return;
  prepareWakeInputs();
  vTaskSuspend(renderTaskHandle);
  // The loop task is the only task watchdog subscriber (like the proven v6
  // firmware): a sleep longer than the watchdog period would panic-reboot, so
  // the current (loop) task leaves the WDT before light sleep and re-arms it
  // right after wake.
  esp_task_wdt_delete(NULL);
  WiFi.setSleep(true);
  const int64_t sleepStartUs = esp_timer_get_time();
  esp_light_sleep_start();
  const int64_t wakeUs = esp_timer_get_time();
  const uint32_t wakeMs = millis();
  restoreAfterWake(wakeMs, static_cast<uint64_t>(wakeUs - sleepStartUs));
  awakeStartedMs = wakeMs;
  // A wake is activity: restart the idle clock. Without this the pad woke with
  // the idle timer still expired, slept again ~3 s later, and spent most of its
  // time in the pre-sleep state (matrix rows driven LOW) — one key press was
  // then read in all three rows (observed live: r0c3+r1c3+r2c3 for one press).
  lastActivityMs = wakeMs;
  processWakeInput(wakeMs);
}

// Post-wake restore: resume the renderer, re-arm this task on the watchdog
// (the loop task is the subscriber), restore the matrix rows to their HIGH
// idle state, invalidate a likely-stale MQTT socket when the sleep outlasted
// the 15 s keepalive (otherwise the socket stays untouched) and schedule an
// immediate reconnect, re-prime every debounce candidate so the wake press is
// delivered exactly once, and feed the encoder's current phase once while its
// pre-sleep phase is preserved.
void restoreAfterWake(uint32_t wakeMs, uint64_t sleptUs) {
  vTaskResume(renderTaskHandle);
  esp_task_wdt_add(NULL);
  // Back to full speed — proven v6 behaviour: the pre-sleep modem sleep must
  // be turned off again, otherwise every MQTT packet after the wake is
  // throttled and the pad feels laggy.
  WiFi.setSleep(false);
  // Disable the per-pin GPIO wake configuration so the matrix/encoder pins
  // return to their ordinary GPIO direction/pullup registers (the pins were
  // INPUT_PULLUP wake candidates during sleep).
  esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_GPIO);
  // Fully re-establish the input pin configuration. Light sleep leaves the
  // wake-capable pins in the low-power wake configuration, and a partially
  // configured matrix reads phantom keys: one press becomes two actions
  // (observed live: back immediately followed by enter on the first row).
  setupInputPins();
  // Let rows/columns settle after the wake before any key is sampled.
  vTaskDelay(pdMS_TO_TICKS(20));
  for (uint8_t row = 0; row < micropad::MATRIX_ROWS; ++row) {
    digitalWrite(ROW_PINS[row], HIGH);
  }
  if (sleptUs > MQTT_KEEPALIVE_US) {
    mqttNetworkClient.stop();
    mqttNextAttemptMs = wakeMs - micropad::MQTT_RETRY_MS;
  }
  for (uint8_t index = 0; index < micropad::MATRIX_KEY_COUNT; ++index) {
    const uint8_t row = static_cast<uint8_t>(index / micropad::MATRIX_COLS);
    const uint8_t col = static_cast<uint8_t>(index % micropad::MATRIX_COLS);
    matrixDebounce[index].primeForWake(readMatrixKey(row, col), wakeMs);
  }
  // Re-baseline the encoder phase after the wake instead of feeding it as a
  // step: a wake-time sample is not a rotation and must not emit a phantom
  // scroll. A real turn still produces detents from the next phase changes.
  const uint8_t phase = static_cast<uint8_t>(
      (digitalRead(ENCODER_A_PIN) << 1) | digitalRead(ENCODER_B_PIN));
  encoderDecoder.reset(phase);
}

// Immediate wake input: a fresh active-low pass emits every currently pressed
// matrix key through the single input seam, so the press that woke the device
// performs its action immediately. The re-primed debounce keeps that same key
// from being re-emitted or silently re-baselined by the following scans.
void processWakeInput(uint32_t wakeMs) {
  // Only a genuine external wake is a key press. A sleep that returned without
  // a wake cause is not input, and the idle clock was already restarted by the
  // caller, so no phantom action or immediate re-sleep can follow.
  if (esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_UNDEFINED) return;
  for (uint8_t index = 0; index < micropad::MATRIX_KEY_COUNT; ++index) {
    const uint8_t row = static_cast<uint8_t>(index / micropad::MATRIX_COLS);
    const uint8_t col = static_cast<uint8_t>(index % micropad::MATRIX_COLS);
    if (!readMatrixKey(row, col)) continue;
    emitInput(micropad::InputEvent{static_cast<micropad::KeyId>(index), true,
                                   0, wakeMs});
    // Exactly one wake press: a wake-time scan that has not fully settled can
    // report a second (phantom) key, which would execute a second action.
    return;
  }
}

// ============================================================================
// Snapshot composition and the pinned Core 0 render task (Task 8). The
// snapshot carries copied strings/scalars only: no pointers into the catalog,
// JSON document, or network objects. Rendering to the panel is the exclusive
// Core 0 display seam (implemented by Task 9); no input, MQTT, portal, or
// NVS function ever runs on Core 0.
// ============================================================================

void buildSnapshot(micropad::RenderSnapshot &out) {
  out.generation = snapshotMailbox.currentGeneration() + 1;
  out.portal = portalActive;
  out.usbHost = stableUsbHost;
  out.networkState = currentNetworkState();
  out.rowCount = 0;
  out.totalItems = 0;
  out.firstVisible = 0;
  safeCopy(out.title, "");
  safeCopy(out.portalSsid, "");
  safeCopy(out.portalPassword, "");
  safeCopy(out.portalAddress, "");
  if (portalActive) {
    safeCopy(out.portalSsid, portalView.ssid);
    safeCopy(out.portalPassword, portalView.password);
    safeCopy(out.portalAddress, portalView.address);
    return;
  }
  const micropad::Page *page = pad.currentPage();
  if (page == nullptr) return;
  safeCopy(out.title, page->title);
  out.totalItems = page->itemCount;
  out.firstVisible = pad.state.firstVisible;
  const uint8_t visible = static_cast<uint8_t>(micropad::VISIBLE_ROWS);
  for (uint8_t i = 0; i < visible; ++i) {
    const uint8_t itemIndex = static_cast<uint8_t>(pad.state.firstVisible + i);
    if (itemIndex >= page->itemCount) break;
    const micropad::Item &item = page->items[itemIndex];
    micropad::RenderRow &row = out.rows[i];
    safeCopy(row.name, item.name);
    safeCopy(row.state, item.state);
    safeCopy(row.unit, item.unit);
    row.value = item.value;
    row.selected = (itemIndex == pad.state.selected);
    row.editing = row.selected && pad.state.editing;
    ++out.rowCount;
  }
}

// Pinned to Core 0 (xTaskCreatePinnedToCore(..., 0)). Blocks on the task
// notification instead of polling, spaces consecutive partial refreshes with
// the wrap-safe MIN_REFRESH_SPACING_MS gate (waiting BEFORE the claim so Core 1
// can keep replacing the still-published slot and the eventual claim receives
// the newest snapshot), claims the newest published snapshot under the mutex,
// publishes renderBusy with release semantics while the display draws,
// releases the slot, and immediately checks once for a newer published
// generation before blocking again so a refresh burst is drained without
// waking the task repeatedly. Sleep reads renderBusy with acquire semantics
// to refuse light sleep mid-refresh. No input, MQTT, portal, or NVS function
// runs on Core 0; nothing on this path prints to Serial.
void renderTask(void *) {
  for (;;) {
    // Bounded wait, never portMAX_DELAY: the renderer stays defensively
    // watchdog-friendly even though only the loop task is subscribed. A short
    // timeout lets the idle path pet the watchdog if the renderer is ever
    // subscribed again; the wait is still a notification wait, not polling.
    if (ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(200)) == 0) {
      esp_task_wdt_reset();
      continue;
    }
    for (;;) {
      waitForRefreshSpacing();
      int8_t slot = -1;
      portENTER_CRITICAL(&snapshotMux);
      slot = snapshotMailbox.claimNewest();
      portEXIT_CRITICAL(&snapshotMux);
      if (slot < 0) break;  // no newer published generation: block again
      renderBusy.store(true, std::memory_order_release);
      esp_task_wdt_reset();  // pet before the long panel draw
      drawSnapshot(snapshotMailbox.readable(slot));
      esp_task_wdt_reset();  // pet after the long panel draw
      renderBusy.store(false, std::memory_order_release);
      portENTER_CRITICAL(&snapshotMux);
      snapshotMailbox.release(slot);
      portEXIT_CRITICAL(&snapshotMux);
    }
  }
}

// Called by setup() after display initialization (Task 12 integration).
void createRenderTask() {
  xTaskCreatePinnedToCore(renderTask, "epaper", 6144, nullptr, 1,
                          &renderTaskHandle, 0);
}

// ============================================================================
// Full-panel landscape rendering (Task 9). initDisplay() runs once at boot;
// every claimed snapshot is drawn on pinned Core 0 through the
// firstPage/nextPage loop. The display physics are non-negotiable and pinned
// by static contracts: exactly one partial window, the full native 128x296
// panel window (0, 0, 128, 296); display.init(0); DISABLE_DIAGNOSTIC_OUTPUT
// (top of file); and no Serial anywhere on the render/refresh path (USB-CDC
// output blocks forever with no host attached). All layout geometry comes
// from the dependency-free core builders into a deterministic prim model;
// this block only translates prims into GxEPD2 draw calls.
// ============================================================================

// The SSD1680 stores partial windows in native (unrotated) coordinates and
// the driver clamps a window against the current rotation's width/height, so
// the full native panel window is taken while rotation is 0 and the 296x128
// landscape user space is restored for firstPage/nextPage drawing. GxEPD2's
// setFullWindow() is rotation-independent (it addresses WIDTH x HEIGHT
// directly); a partial must re-arm the window exactly this way.
void setFullPanelPartialRefresh() {
  display.setRotation(0);
  display.setPartialWindow(0, 0, 128, 296);
  display.setRotation(1);
}

void initDisplay() {
  SPI.begin(PIN_EPD_SCK, -1, PIN_EPD_MOSI, -1);
  display.init(0);
  // Boot window state is the constructor's full native panel window
  // (setFullWindow: 0, 0, 128, 296); the only partial-window call in the
  // sketch lives on the draw path in setFullPanelPartialRefresh().
  // setRotation(1) maps the 296x128 landscape user space onto the full
  // native 128x296 SSD1680 panel.
  display.setRotation(1);
  display.setTextColor(GxEPD_BLACK);
}

// Core 0 refresh spacing gate, called before each claim. A partial start
// checks the wrap-safe elapsed time against MIN_REFRESH_SPACING_MS and waits
// only for the remaining interval; forced-full starts (mirrored from the
// newest published snapshot) never lag. Waiting before the claim lets Core 1
// keep replacing the still-published slot so the eventual claim receives the
// newest snapshot. vTaskDelay blocks only this task; nothing prints.
void waitForRefreshSpacing() {
  const bool partialImminent =
      !pendingForceFull.load(std::memory_order_acquire) &&
      refreshPolicy.partialCount() < micropad::MAX_PARTIAL_REFRESHES;
  if (!partialImminent) return;
  const uint32_t remaining = refreshPolicy.remainingSpacingMs(millis());
  if (remaining > 0) vTaskDelay(pdMS_TO_TICKS(remaining));
}

// One text span: exact vertical centering inside the prim's box and exact
// right alignment come from the real font metrics, so the clip math in the
// core (MONO_CHAR_W) is only used for layout decisions, never for pixel
// anchors.
void drawTextPrim(const micropad::RenderPrim &prim) {
  if (prim.text[0] == '\0') return;
  display.setFont(&FreeMonoBold9pt7b);
  display.setTextColor(GxEPD_BLACK);
  int16_t textX = 0;
  int16_t textY = 0;
  uint16_t textW = 0;
  uint16_t textH = 0;
  display.getTextBounds(prim.text, 0, 0, &textX, &textY, &textW, &textH);
  const int16_t baseline = prim.y0 + (prim.h - static_cast<int16_t>(textH)) / 2
                           - textY;
  int16_t cursorX = prim.x0;
  if (prim.align == static_cast<uint8_t>(micropad::TextAlign::Right)) {
    cursorX = prim.x0 - static_cast<int16_t>(textW);
  }
  display.setCursor(cursorX, baseline);
  display.print(prim.text);
}

void drawRenderModel(const micropad::RenderModel &model) {
  for (uint16_t i = 0; i < model.count; ++i) {
    const micropad::RenderPrim &prim = model.prims[i];
    const uint16_t color = prim.color == micropad::PRIM_COLOR_WHITE
                               ? GxEPD_WHITE
                               : GxEPD_BLACK;
    switch (prim.kind) {
      case micropad::PrimKind::Line:
        display.drawLine(prim.x0, prim.y0, prim.x1, prim.y1, GxEPD_BLACK);
        break;
      case micropad::PrimKind::Rect:
        display.drawRect(prim.x0, prim.y0, prim.w, prim.h, GxEPD_BLACK);
        break;
      case micropad::PrimKind::FillRect:
        display.fillRect(prim.x0, prim.y0, prim.w, prim.h, color);
        break;
      case micropad::PrimKind::Circle:
        display.drawCircle(prim.x0, prim.y0, prim.radius, color);
        break;
      case micropad::PrimKind::FillCircle:
        display.fillCircle(prim.x0, prim.y0, prim.radius, color);
        break;
      case micropad::PrimKind::FillTriangle:
        display.fillTriangle(prim.x0, prim.y0, prim.x1, prim.y1, prim.w,
                             prim.h, GxEPD_BLACK);
        break;
      case micropad::PrimKind::Text:
        drawTextPrim(prim);
        break;
    }
  }
}

// Network status cell geometry (solid square / ring / two dots) is built by
// the dependency-free networkStatusPrims and only translated here; no font or
// text call lives in this seam.
void drawNetworkStatus(uint8_t networkState) {
  micropad::RenderModel model;
  micropad::networkStatusPrims(networkState, model);
  drawRenderModel(model);
}

// USB power symbol: pure line/rectangle/circle geometry from the core builder
// (powerIconPrims); deliberately no text or glyph call in this seam.
void drawPowerSymbol(bool usbHost) {
  micropad::RenderModel model;
  micropad::powerIconPrims(usbHost, model);
  drawRenderModel(model);
}

void drawNormalUi(const micropad::RenderSnapshot &snap) {
  micropad::RenderModel model;
  micropad::layoutNormalUi(snap, model);
  drawRenderModel(model);
  drawNetworkStatus(snap.networkState);
  if (snap.usbHost) drawPowerSymbol(true);
}

void drawPortalUi(const micropad::RenderSnapshot &snap) {
  micropad::RenderModel model;
  micropad::layoutPortalUi(snap, model);
  drawRenderModel(model);
  drawNetworkStatus(snap.networkState);
  if (snap.usbHost) drawPowerSymbol(true);
}

// Sample the panel BUSY line after the refresh with a wrap-safe 15 s timeout.
// No Serial, no input or network call: the render task only touches the
// display and the refresh policy here. BUSY is HIGH while the SSD1680 is busy
// (matches the busy_level HIGH constructor argument), so LOW means ready.
bool waitForPanelReady() {
  const uint32_t startMs = millis();
  while (micropad::elapsedMs(millis(), startMs) <
         micropad::DISPLAY_BUSY_TIMEOUT_MS) {
    if (digitalRead(PIN_EPD_BUSY) == LOW) return true;
    esp_task_wdt_reset();  // BUSY can hold > 5 s: pet the task watchdog
    vTaskDelay(pdMS_TO_TICKS(5));
  }
  return false;
}

// BUSY anomaly recovery on Core 0: mark recovery so the next snapshot is
// forced through the full path, then re-run the display initialization
// (init(0) + full native window + landscape rotation). Input and network code
// stay on Core 1 and never run while this task recovers.
void recoverDisplay() {
  refreshPolicy.requireRecoveryFull();
  initDisplay();
  // Signal the Core 1 loop so renderRecoveryTick requests the forced-full
  // redraw on the next pass (Core 0 never publishes snapshots itself).
  recoveryDrawNeeded.store(true, std::memory_order_release);
}

// The Task 9 display seam (see the Task 8 render block): draws one immutable
// snapshot on Core 0. Decides full vs partial through the core RefreshPolicy
// (forced full at boot, portal transitions, BUSY recovery, and the partial
// limit), runs the firstPage/nextPage loop once, then conservatively samples
// BUSY and records the outcome. The full native panel is addressed in both
// modes, so partial refreshes never use per-row band windows.
void drawSnapshot(const micropad::RenderSnapshot &snap) {
  const micropad::RefreshMode mode =
      refreshPolicy.beginRefresh(millis(), snap.forceFull);
  if (mode == micropad::RefreshMode::Full) {
    display.setFullWindow();
  } else {
    setFullPanelPartialRefresh();
  }
  display.firstPage();
  do {
    display.fillScreen(GxEPD_WHITE);
    if (snap.portal) {
      drawPortalUi(snap);
    } else {
      drawNormalUi(snap);
    }
  } while (display.nextPage());
  const bool panelReady = waitForPanelReady();
  refreshPolicy.completeRefresh(panelReady);
  if (!panelReady) recoverDisplay();
}

// ============================================================================
// Core 1 loop integration (Task 12). All earlier subsystems are wired here:
// setup() initializes hardware, stored settings, network adapters, and the
// display in the pinned order; loop() runs one nonblocking cooperative pass.
// Rendering is absent from loop(): draw requests come only from state-change
// edges, accepted MQTT payloads, stable power edges, portal transitions,
// boot, and display recovery.
// ============================================================================

// Core 1 half of the BUSY-anomaly recovery handshake. recoverDisplay() on
// Core 0 sets the flag after re-initializing the panel; this tick consumes it
// exactly once and requests the forced-full redraw through the normal mailbox
// publisher. A fresh recovery while a refresh is in flight coalesces into the
// mailbox burst like every other draw request.
void renderRecoveryTick(uint32_t nowMs) {
  (void)nowMs;  // canonical tick signature; recovery needs no timing decision
  if (recoveryDrawNeeded.exchange(false, std::memory_order_acquire)) {
    requestDraw(micropad::DrawReason::Recovery);
  }
}

// 4a: CDC guards, active-low input pins, the default keymap, and stored
// settings (Preferences); 4b: MQTT callback/buffer, display init, the pinned
// Core 0 render task, its watchdog registration, then the boot full refresh;
// 4c: first-boot portal or station mode with one asynchronous attempt.
// createRenderTask() ALWAYS precedes the first requestDraw (boot refresh and
// portal start both notify the render-task handle).
void setup() {
  configureCdc();
  setupInputPins();
  loadDefaultKeymap(pad.activeKeymap);
  safeCopy(pad.state.pageId, "home");
  const bool settingsLoaded = loadSettings();
#if MICROPAD_DEBUG
  assert(xPortGetCoreID() == 1);  // setup must run on the application core
#endif

  mqttClient.setBufferSize(micropad::MQTT_BUFFER_BYTES);
  mqttClient.setSocketTimeout(1);
  mqttNetworkClient.setConnectionTimeout(100);
  mqttClient.setServer(deviceSettings.mqttHost, deviceSettings.mqttPort);
  mqttClient.setCallback(onMqttMessage);
  mqttClientConfigured = true;
  initDisplay();
  createRenderTask();
  // Subscribe the loop task (this task) to the task watchdog and feed it on
  // every loop() pass; the render task is deliberately left unsubscribed so
  // long GxEPD2 panel busy-waits on Core 0 never trip the TWDT.
  esp_task_wdt_add(NULL);

  // Activity and USB-sample clocks seeded at boot: the first powerTick waits
  // one full 500 ms interval and the 3000 ms post-wake minimum-awake brake
  // counts from the boot instant (F10/F11 carries).
  awakeStartedMs = millis();
  lastUsbSampleMs = millis();

  // No stored settings -> first-boot setup portal (which forces its own full
  // refresh); otherwise station mode with one asynchronous Wi-Fi attempt
  // scheduled now. Nothing waits for a connection: wifiTick throttles retries
  // from wifiLastAttemptMs.
  if (!settingsLoaded) {
    startPortal(millis());
    return;
  }
  WiFi.mode(WIFI_STA);
  wifiLastAttemptMs = millis();
  WiFi.begin(deviceSettings.wifiSsid, deviceSettings.wifiPassword);
}

// Canonical cooperative loop (Task 12, brief step 5): exactly one nonblocking
// pass in the pinned order. Ticks gate themselves by time and state; the
// connected MQTT client is pumped once per pass; the event queue flushes at
// most MAX_FLUSH_PER_LOOP; quiet-period resync, the 500 ms USB sample, and
// display recovery all get their turn; a USB-free idle renderer sleeps.
void loop() {
  esp_task_wdt_reset();  // feed the loop task's watchdog subscription
  const uint32_t nowMs = millis();
  scanMatrix(nowMs);
  scanEncoder(nowMs);
  portalTick(nowMs);
  wifiTick(nowMs);
  mqttTick(nowMs);
  if (mqttClient.connected()) mqttClient.loop();
  flushEventQueue();
  resyncTick(nowMs);
  powerTick(nowMs);
  renderRecoveryTick(nowMs);
  // Light sleep is disabled until the ESP32-S3 wake path is fixed: hardware
  // evidence shows that after a wake the matrix rows stay in their pre-sleep
  // LOW state, so one key press is read in all three rows (phantom actions).
  if (micropad::LIGHT_SLEEP_ENABLED && sleepGateReady(nowMs)) {
    enterLightSleep(nowMs);
  }
}
