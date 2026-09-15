# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Static contract tests for the clean-room MicroPad firmware sketch.

These assert on the source text of ``firmware/MicroPad_HA_Controller.ino`` and
the host core header, so no Arduino toolchain is required.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]


class FirmwareStaticContractTest(unittest.TestCase):
    def source(self, relative: str) -> str:
        return (PROJECT_ROOT / relative).read_text(encoding="utf-8")

    @staticmethod
    def function_body(text: str, signature: str) -> str:
        """Return the brace-delimited body of the function whose definition
        begins with ``signature``, using a brace-depth scanner so nested
        blocks (if/for/compound literals) are handled correctly. The last
        occurrence of ``signature`` is used (the text may contain earlier
        forward declarations, which have no body)."""
        start = text.rindex(signature)
        open_brace = text.index("{", start + len(signature))
        depth = 0
        for index in range(open_brace, len(text)):
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[open_brace + 1 : index]
        raise AssertionError(f"unterminated body for {signature}")

    def test_loop_task_stack_size_raised(self):
        # The loop stack covers the MQTT callback frame. The callback no longer
        # copies the payload onto it (it parses the PubSubClient buffer in
        # place), which shrank the measured frame from 23,424 to ~7 KB, so the
        # documented 16 KiB keeps a ~2.3x margin over the frame while returning
        # 16 KiB to the internal heap.
        text = self.source("firmware/MicroPad_HA_Controller.ino")
        core = self.source("firmware/micropad_core.h")
        self.assertIn(
            "size_t getArduinoLoopTaskStackSize(void) { return micropad::LOOP_TASK_STACK_BYTES; }",
            text,
        )
        # One definition, in the core, so the device-info payload and the tests
        # report the same number the linker reserves.
        self.assertIn("constexpr size_t LOOP_TASK_STACK_BYTES = 16384;", core)

    def test_exact_hardware_pins(self):
        text = self.source("firmware/MicroPad_HA_Controller.ino")
        self.assertIn("constexpr uint8_t PIN_EPD_CS = 8;", text)
        self.assertIn("constexpr uint8_t PIN_EPD_DC = 40;", text)
        self.assertIn("constexpr uint8_t PIN_EPD_RST = 41;", text)
        self.assertIn("constexpr uint8_t PIN_EPD_BUSY = 42;", text)
        self.assertIn("constexpr uint8_t PIN_EPD_SCK = 39;", text)
        self.assertIn("constexpr uint8_t PIN_EPD_MOSI = 38;", text)
        self.assertIn("constexpr uint8_t ROW_PINS[3] = {47, 48, 45};", text)
        self.assertIn("constexpr uint8_t COL_PINS[4] = {21, 14, 13, 12};", text)
        self.assertIn("constexpr uint8_t ENCODER_A_PIN = 10;", text)
        self.assertIn("constexpr uint8_t ENCODER_B_PIN = 11;", text)

    def test_input_pins_safe_pullups(self):
        text = self.source("firmware/MicroPad_HA_Controller.ino")
        body = self.function_body(text, "void setupInputPins()")
        self.assertIn("pinMode(ROW_PINS[row], OUTPUT);", body)
        self.assertIn("digitalWrite(ROW_PINS[row], HIGH);", body)
        self.assertIn("pinMode(COL_PINS[col], INPUT_PULLUP);", body)
        self.assertIn("pinMode(ENCODER_A_PIN, INPUT_PULLUP);", body)
        self.assertIn("pinMode(ENCODER_B_PIN, INPUT_PULLUP);", body)
        # Columns and encoder pins pull up; no column or encoder pin is OUTPUT.
        self.assertNotIn("COL_PINS[col], OUTPUT", body)
        self.assertNotIn("COL_PINS[col], LOW", body)

    def test_read_matrix_key_restores_row(self):
        text = self.source("firmware/MicroPad_HA_Controller.ino")
        body = self.function_body(text, "bool readMatrixKey(uint8_t row, uint8_t col)")
        low = body.index("digitalWrite(ROW_PINS[row], LOW);")
        read = body.index("digitalRead(COL_PINS[col]) == LOW")
        high = body.index("digitalWrite(ROW_PINS[row], HIGH);")
        self.assertLess(low, read)
        self.assertLess(read, high)
        self.assertNotIn("delay(", body)
        self.assertNotIn("while (", body)

    def test_scanners_emit_only(self):
        # Input leaves the scanners only through the single emitInput seam. The
        # matrix scanner must not touch pins directly (it routes through the
        # active-low adapter); the encoder scanner may read A/B but must never
        # drive pins or delay, and stays nonblocking.
        text = self.source("firmware/MicroPad_HA_Controller.ino")
        body = self.function_body(text, "void scanMatrix(uint32_t nowMs)")
        self.assertIn("emitInput", body)
        self.assertNotIn("digitalWrite", body)
        self.assertNotIn("digitalRead", body)
        self.assertNotIn("delay(", body)
        self.assertIn("nowMs", body)

        body = self.function_body(text, "void scanEncoder(uint32_t nowMs)")
        self.assertIn("emitInput", body)
        self.assertIn("digitalRead(ENCODER_A_PIN", body)
        self.assertIn("digitalRead(ENCODER_B_PIN", body)
        self.assertNotIn("digitalWrite", body)
        self.assertNotIn("delay(", body)
        self.assertIn("nowMs", body)

    def test_display_diagnostics_are_disabled_before_include(self):
        text = self.source("firmware/MicroPad_HA_Controller.ino")
        define_at = text.index("#define DISABLE_DIAGNOSTIC_OUTPUT")
        include_at = text.index("#include <GxEPD2_BW.h>")
        self.assertLess(define_at, include_at)


class FirmwareSettingsContractTest(FirmwareStaticContractTest):
    """Persistence contract: neutral defaults, NVS keys, validate-before-write.

    The dependency-free ``Settings`` type, its neutral defaults, and the
    validation rule live in the host-testable core (``micropad_core.*``) so the
    logic is proven without NVS; the Preferences-backed ``loadSettings`` and
    ``saveSettings`` live in the Arduino sketch where NVS is available and are
    pinned by static source checks.
    """

    def ino(self) -> str:
        return self.source("firmware/MicroPad_HA_Controller.ino")

    def core_source(self) -> str:
        header = self.source("firmware/micropad_core.h")
        impl = self.source("firmware/micropad_core.cpp")
        return header + "\n" + impl

    def test_neutral_defaults_source_literals(self):
        # Broker, port, user, and password are placeholders only in source;
        # real credentials are never compiled in, only entered via portal/prefs.
        text = self.core_source()
        self.assertIn('"192.168.0.100"', text)
        self.assertIn("1883", text)
        self.assertIn('"micropad"', text)
        self.assertIn('"replace-me"', text)

    def test_no_real_ip_literal_beyond_placeholder(self):
        # Only the documented 192.168.0.100 placeholder IP may appear; parse
        # every IPv4 literal and reject anything else (all private ranges and
        # any real address). Dotted ports like MQTT's 1883 never match.
        import re

        text = self.core_source()
        ips = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
        for ip in ips:
            octets = [int(o) for o in ip.split(".")]
            if any(o > 255 for o in octets):
                continue  # not a valid IPv4 literal
            self.assertEqual(
                ip, "192.168.0.100", f"unexpected IPv4 literal {ip}"
            )

    def test_preferences_namespace_and_keys(self):
        text = self.ino()
        self.assertIn('"micropad"', text)
        for key in ("wifi_ssid", "wifi_pass", "mqtt_host", "mqtt_port",
                    "mqtt_user", "mqtt_pass", "client_id", "cfg_ver"):
            self.assertIn(f'"{key}"', text)

    def test_save_validates_before_write_and_round_lossless(self):
        body = self.function_body(
            self.ino(), "bool saveSettings(const micropad::Settings &settings)"
        )
        # A complete candidate validates before NVS is opened read-write.
        validate_at = body.index("validateSettings(")
        begin_at = body.index("preferences.begin(")
        self.assertLess(validate_at, begin_at)
        # No logging call can sink a credential.
        self.assertNotIn("Serial", body)
        self.assertNotIn("println", body)
        self.assertNotIn("printf", body)
        self.assertNotIn("log(", body)
        # The commit marker is invalidated BEFORE the field writes and set to 1
        # only after every field was stored: a save that fails midway must not
        # leave a mixture of old and new values that still validates on the next
        # boot (the pad would associate with a mismatched record and never
        # reopen its setup portal).
        self.assertIn('putUChar("cfg_ver", 0)', body)
        invalidate_at = body.index('putUChar("cfg_ver", 0)')
        for cred in ("wifi_ssid", "wifi_pass", "mqtt_host", "mqtt_port",
                     "mqtt_user", "mqtt_pass", "client_id"):
            cred_at = body.index(f'"{cred}"')
            self.assertGreater(cred_at, invalidate_at,
                               f"{cred} must be written after the marker reset")
        self.assertLess(invalidate_at, body.index('putUChar("cfg_ver", 1)'))
        self.assertNotIn('preferences.clear()', body)
        self.assertIn("preferences.end()", body)
        self.assertIn('putUChar("cfg_ver", 1) > 0', body)
        self.assertIn('putSettingsString(preferences, "mqtt_pass"', body)
        self.assertIn("mqttClientConfigured = false", body)
        self.assertNotIn("ok &= preferences.putString", body)

    def test_load_readonly_and_ends_on_every_path(self):
        body = self.function_body(self.ino(), "bool loadSettings()")
        self.assertIn("preferences.begin(\"micropad\", true)", body)
        self.assertIn("preferences.end()", body)
        self.assertIn("validateSettings(", body)
        self.assertIn('"cfg_ver"', body)


class FirmwarePortalContractTest(FirmwareStaticContractTest):
    """Captive portal contract: hardware entropy, one-pass nonblocking
    service, validate-before-save, and portal-mode key handling (Task 6)."""

    def ino(self) -> str:
        return self.source("firmware/MicroPad_HA_Controller.ino")

    def test_setup_password_uses_hardware_entropy(self):
        text = self.ino()
        core = self.source("firmware/micropad_core.h")
        self.assertIn("constexpr size_t SETUP_PASSWORD_LENGTH = 16;", core)
        self.assertIn("#include <esp_system.h>", text)
        self.assertIn("static_assert(sizeof(SETUP_ALPHABET) - 1 == 64)", text)
        body = self.function_body(text, "void generateSetupPassword(")
        # Random bytes are masked to 6 bits and index the 64-character
        # alphabet: hardware entropy only, no modulo bias, no substitute.
        self.assertIn("esp_fill_random(randomBytes", body)
        self.assertIn("SETUP_ALPHABET[randomBytes[i] & 0x3f]", body)
        # The only random() occurrence is the esp_fill_random hardware call:
        # no timestamp, MAC, or seeded PRNG substitute.
        self.assertEqual(body.count("random("), body.count("esp_fill_random("))
        self.assertNotIn("millis(", body)
        self.assertNotIn("micros(", body)
        self.assertNotIn("macAddress", body)

    def test_setup_alphabet_is_64_characters(self):
        text = self.ino()
        self.assertIn(
            '"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
            '-_"',
            text,
        )

    def test_portal_tick_services_once_and_never_blocks(self):
        body = self.function_body(self.ino(), "void portalTick(uint32_t nowMs)")
        self.assertEqual(body.count("dnsServer.processNextRequest()"), 1)
        self.assertEqual(body.count("webServer.handleClient()"), 1)
        self.assertNotIn("while ", body)
        self.assertNotIn("delay(", body)
        self.assertNotIn("Serial", body)
        self.assertNotIn("println", body)
        self.assertNotIn("printf", body)

    def test_portal_tick_restarts_only_after_idle_timeout(self):
        body = self.function_body(self.ino(), "void portalTick(uint32_t nowMs)")
        self.assertIn("micropad::PORTAL_IDLE_MS", body)
        self.assertIn("ESP.restart()", body)
        idle_at = body.index(
            "static_cast<uint32_t>(nowMs - portalLastActivityMs)")
        self.assertLess(idle_at, body.index("ESP.restart()"))
        # Issue #6: the restart is a client-free safety net only. A connected
        # setup client is an active user, so the pad must never reset itself
        # while the phone is attached, and the idle window is generous enough
        # that a bumped pairing session does not lose the entered settings.
        self.assertIn("WiFi.softAPgetStationNum() > 0", body)
        self.assertLess(body.index("WiFi.softAPgetStationNum() > 0"),
                        body.index("ESP.restart()"))
        self.assertIn("!setupClientAttached", body)
        self.assertIn("constexpr uint32_t PORTAL_IDLE_MS = 1800000;",
                      self.source("firmware/micropad_core.h"))

    def test_portal_activity_refreshes_on_requests_and_input(self):
        text = self.ino()
        for signature in ("void handlePortalGet()", "void handlePortalPost()"):
            self.assertIn("portalLastActivityMs",
                          self.function_body(text, signature))
        self.assertIn(
            "portalLastActivityMs",
            self.function_body(
                text, "void emitInput(const micropad::InputEvent &input)"),
        )

    def test_post_validates_every_field_before_save(self):
        body = self.function_body(self.ino(), "void handlePortalPost()")
        save_at = body.index("saveSettings(")
        self.assertLess(body.index("validateSettings("), save_at)
        for field in ("candidate.wifiSsid", "candidate.wifiPassword",
                      "candidate.mqttHost", "candidate.mqttPort",
                      "candidate.mqttUser", "candidate.mqttPassword",
                      "candidate.clientId"):
            self.assertLess(body.index(field), save_at)
        # Every HTTP 400 rejection precedes the save.
        search_from = 0
        rejections = 0
        while True:
            found = body.find("webServer.send(400", search_from)
            if found == -1:
                break
            rejections += 1
            self.assertLess(found, save_at)
            search_from = found + 1
        self.assertGreaterEqual(rejections, 3)

    def test_post_success_responds_then_sets_exit_flag(self):
        body = self.function_body(self.ino(), "void handlePortalPost()")
        save_at = body.index("saveSettings(")
        last_send = body.rindex("send(")
        exit_at = body.index("portalExitSaved = true")
        self.assertLess(save_at, last_send)
        self.assertLess(last_send, exit_at)

    def test_cancel_portal_leaves_settings_untouched(self):
        body = self.function_body(self.ino(), "void cancelPortal()")
        self.assertNotIn("Preferences", body)
        self.assertNotIn("preferences", body)
        self.assertNotIn("saveSettings", body)
        self.assertIn("stopPortal(false)", body)

    def test_stop_portal_tears_down_and_reconnects_only_on_save(self):
        body = self.function_body(self.ino(), "void stopPortal(bool saved)")
        self.assertNotIn("preferences", body)
        self.assertNotIn("saveSettings", body)
        self.assertIn("webServer.stop()", body)
        self.assertIn("dnsServer.stop()", body)
        self.assertIn("WiFi.softAPdisconnect", body)
        self.assertIn("WIFI_STA", body)
        self.assertIn("PortalExit", body)
        self.assertLess(body.index("if (saved)"), body.index("WiFi.begin("))

    def test_back_exits_portal_only_while_active(self):
        body = self.function_body(
            self.ino(), "void emitInput(const micropad::InputEvent &input)")
        cancel_at = body.index("cancelPortal()")
        self.assertLess(body.index("if (portalActive)"), cancel_at)
        self.assertLess(body.index("Action::Back"), cancel_at)
        # Portal-mode input refreshes the inactivity clock.
        self.assertLess(body.index("portalLastActivityMs"), cancel_at)
        # Settings reaches the portal through normal keymap resolution.
        self.assertLess(body.index("Action::Settings"),
                        body.index("startPortal("))

    def test_start_portal_ap_dns_and_routes_once(self):
        body = self.function_body(self.ino(), "void startPortal(uint32_t nowMs)")
        self.assertIn('WiFi.softAP("MicroPad-Setup", setupPassword)', body)
        self.assertIn("WIFI_AP", body)
        self.assertIn("WiFi.softAPIP()", body)
        self.assertIn("webServer.begin()", body)
        self.assertIn('webServer.on("/"', body)
        self.assertLess(body.index("if (!portalRoutesRegistered)"),
                        body.index("portalRoutesRegistered = true"))
        # Portal entry never waits for clients.
        self.assertNotIn("while ", body)
        self.assertNotIn("delay(", body)

    def test_setup_password_loaded_before_generate_on_entry(self):
        # Issue #7: startPortal must try to load a password saved on the
        # non-volatile storage and only generate + store one when none exists,
        # so that the same pad always uses the same AP password.
        text = self.ino()
        self.assertIn("bool loadSetupPassword(", text)
        self.assertIn("void saveSetupPassword(", text)
        self.assertIn('preferences.getString("setup_pass"', text)
        # The load path must accept the nvs_get_str length convention, which
        # counts the NUL terminator: a stored 16-character password reads
        # back as 17. Comparing against the raw constant would make the load
        # always fail and regenerate the password on every boot (issue #7).
        load_body = self.function_body(text, "bool loadSetupPassword(")
        self.assertIn("micropad::SETUP_PASSWORD_LENGTH + 1", load_body)
        self.assertIn('preferences.getString("setup_pass", out, sizeof(out))',
                      load_body)

        body = self.function_body(self.ino(), "void startPortal(uint32_t nowMs)")
        # The load-or-generate decision happens before the AP is brought up.
        self.assertLess(body.index("loadSetupPassword(setupPassword)"),
                        body.index('WiFi.softAP("MicroPad-Setup", setupPassword)'))
        self.assertIn("generateSetupPassword(setupPassword)", body)
        self.assertIn("saveSetupPassword(setupPassword)", body)
        # Generate is only reachable on the not-stored branch.
        gen_at = body.index("generateSetupPassword(setupPassword)")
        load_at = body.index("loadSetupPassword(setupPassword)")
        save_at = body.index("saveSetupPassword(setupPassword)")
        self.assertLess(load_at, gen_at)
        self.assertLess(gen_at, save_at)

    def test_portal_view_state_published(self):
        text = self.ino()
        self.assertIn("struct PortalViewState", text)
        # Only the fields the panel renders are published/copied; the former
        # `active` and `instructions` members were written and never read.
        self.assertNotIn("portalView.instructions", text)
        self.assertNotIn("portalView.active", text)
        self.assertNotIn("char instructions[", text)
        body = self.function_body(self.ino(), "void startPortal(uint32_t nowMs)")
        for field in ("portalView.ssid", "portalView.password",
                      "portalView.address"):
            self.assertIn(f"safeCopy({field},", body)
        self.assertIn('"MicroPad-Setup"', body)
        self.assertIn('"192.168.4.1"', body)
        self.assertIn("micropad::DrawReason::PortalEnter", body)

    def test_get_form_escapes_and_never_prefills_passwords(self):
        text = self.ino()
        escaped = self.function_body(
            text, "void appendEscaped(String &out, const char *text)")
        for entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
            self.assertIn(entity, escaped)
        page = self.function_body(text, "String portalPage()")
        # Password fields are typed password and never carry a pre-fill value.
        self.assertIn("type='password' name='wifi_pass'", page)
        self.assertIn("type='password' name='mqtt_pass'", page)
        # The generated AP password is displayed for pairing.
        self.assertIn("setupPassword", page)

    def test_debug_endpoint_guarded_by_debug_macro(self):
        text = self.ino()
        self.assertLess(text.index("#if MICROPAD_DEBUG"),
                        text.index('webServer.on("/debug"'))
        debug = self.function_body(text, "void handlePortalDebug()")
        self.assertIn("event_queue_overflow=", debug)
        self.assertNotIn("wifi_pass", debug)
        self.assertNotIn("mqtt_pass", debug)


class FirmwareNetworkContractTest(FirmwareStaticContractTest):
    """WiFi/MQTT state machines and the bounded one-pass MQTT callback.

    The dependency-free decisions (event serialization field rules, page/keymap
    commit/swap semantics, retry constants) are host-tested in
    ``test_core.cpp``; these checks pin the sketch-side adapters that touch
    WiFi/PubSubClient/ArduinoJson.
    """

    def ino(self) -> str:
        return self.source("firmware/MicroPad_HA_Controller.ino")

    def test_topic_literals_bound_to_contract_header(self):
        # The six wire topics are defined exactly once, in
        # firmware/protocol_contract.h (the canonical contract binding that
        # firmware, backend, /api/meta, and the browser all share); the sketch
        # includes that header and must not duplicate the literals.
        header = self.source("firmware/protocol_contract.h")
        for topic in ("micropad/event", "micropad/pages/all",
                      "micropad/page/current", "micropad/keymap",
                      "micropad/power", "micropad/device"):
            self.assertIn(f'"{topic}"', header)
            self.assertNotIn(f'"{topic}"', self.ino())

    def test_mqtt_buffer_size_exact(self):
        self.assertIn(
            "mqttClient.setBufferSize(micropad::MQTT_BUFFER_BYTES)",
            self.ino(),
        )

    def test_wifi_retry_bound_and_nonblocking(self):
        body = self.function_body(self.ino(), "void wifiTick(uint32_t nowMs)")
        self.assertIn("micropad::WIFI_RETRY_MS", body)
        self.assertIn("WiFi.begin(", body)
        # Never loops on connection: no wait for WL_CONNECTED, no retry loop.
        self.assertNotIn("while ", body)
        self.assertNotIn("WL_CONNECTED", body)
        self.assertNotIn("delay(", body)

    def test_mqtt_retry_bound_and_single_connect_site(self):
        body = self.function_body(self.ino(), "void mqttTick(uint32_t nowMs)")
        self.assertIn("micropad::MQTT_RETRY_MS", body)
        # At most one connect attempt site; never loops around connect().
        self.assertEqual(body.count("connect("), 1)
        self.assertNotIn("while ", body)
        self.assertNotIn("delay(", body)
        self.assertIn("mqttClient.setSocketTimeout(1)", body)
        self.assertIn("setConnectionTimeout(100)", body)
        self.assertIn("mqttClient.setServer(deviceSettings.mqttHost, deviceSettings.mqttPort)", body)
        self.assertLess(
            body.index("mqttClient.setServer(deviceSettings.mqttHost, deviceSettings.mqttPort)"),
            body.index("mqttClient.connect("),
        )
        # Configured client id is used; else the MAC-derived fallback buffer.
        self.assertIn("deviceSettings.clientId", body)
        self.assertIn('"micropad-%012llx"', body)
        self.assertIn("micropad::CLIENT_ID_CAP", body)

    def test_network_functions_never_block_or_print(self):
        for signature in (
            "void wifiTick(uint32_t nowMs)",
            "void mqttTick(uint32_t nowMs)",
            "void onMqttMessage(char *topic, uint8_t *payload, "
            "unsigned int length)",
            "void flushEventQueue()",
            "void resyncTick(uint32_t nowMs)",
            "void powerTick(uint32_t nowMs)",
        ):
            body = self.function_body(self.ino(), signature)
            self.assertNotIn("while ", body, signature)
            self.assertNotIn("delay(", body, signature)
            self.assertNotIn("Serial", body, signature)
            self.assertNotIn("println", body, signature)
            self.assertNotIn("Serial.write", body, signature)
            self.assertNotIn("display.", body, signature)

    def test_mqtt_callback_bounded_one_pass_envelope(self):
        body = self.function_body(
            self.ino(),
            "void onMqttMessage(char *topic, uint8_t *payload, "
            "unsigned int length)",
        )
        # The payload is parsed exactly once, straight out of the PubSubClient
        # receive buffer: the client keeps it valid for the whole callback and
        # the length is known, so no stack copy and no NUL terminator are
        # needed (they cost 16,385 bytes of permanent loop-task stack for
        # nothing and shrank the heap by as much).
        self.assertEqual(body.count("memcpy"), 0)
        self.assertNotIn("mqttPayload", body)
        self.assertEqual(body.count("deserializeJson"), 1)
        self.assertIn("deserializeJson(doc, payload, length)", body)
        # Oversized payloads are rejected before the parse.
        self.assertIn("length > micropad::MQTT_BUFFER_BYTES", body)
        # Null pointers from a misbehaving client are rejected before use.
        self.assertIn("payload == nullptr", body)
        # Active cache/keymap assignment happens only after parse success.
        self.assertLess(body.index("deserializeJson"),
                        body.index("commitCurrentPage("))
        self.assertLess(body.index("deserializeJson"),
                        body.index("replaceKeymap("))

    def test_event_flush_budget_retain_false_and_restore(self):
        body = self.function_body(self.ino(), "void flushEventQueue()")
        self.assertIn("micropad::MAX_FLUSH_PER_LOOP", body)
        self.assertIn("mp::TOPIC_EVENT", body)
        self.assertIn("publish(mp::TOPIC_EVENT, buffer, false)", body)
        # A failed publish restores the popped event at the queue front.
        self.assertIn("pushFront(event)", body)
        self.assertNotIn("while ", body)
        self.assertNotIn("delay(", body)

    def test_resync_tick_quiet_period_gate(self):
        body = self.function_body(self.ino(), "void resyncTick(uint32_t nowMs)")
        self.assertIn("resyncDue(", body)
        self.assertIn("queueNavigateRequest(", body)
        self.assertIn("resyncPending", body)
        self.assertNotIn("while ", body)


class FirmwareSnapshotContractTest(FirmwareStaticContractTest):
    """Dual-core render snapshot contract: Core 0 render task pinning, the
    mailbox critical-section scope, and immutable snapshot publication (Task 8).

    The two-slot state machine itself is host-tested in ``test_core.cpp``;
    these checks pin the sketch-side adapters that touch FreeRTOS and the
    snapshot-builder I/O.
    """

    def ino(self) -> str:
        return self.source("firmware/MicroPad_HA_Controller.ino")

    def test_atomic_include_and_render_busy_declared(self):
        text = self.ino()
        self.assertIn("#include <atomic>", text)
        self.assertIn("std::atomic<bool> renderBusy{false}", text)

    def test_render_task_pinned_to_core_zero(self):
        text = self.ino()
        # Exactly one render-task creation, on Core 0, with the exact stack
        # size, priority, and handle from the shared interface (the call may
        # be split across two lines by clang-format).
        self.assertEqual(text.count("xTaskCreatePinnedToCore(renderTask,"), 1)
        self.assertIn('"epaper"', text)
        # 8192 (v6-proven): one draw pass holds a 2.4 KB prim model plus the
        # GxEPD2 frames; the former 6144 overflowed and panicked the render
        # task mid-draw (blank panel / reboot, issue #6).
        self.assertIn("8192", text)
        self.assertIn("&renderTaskHandle, 0)", text)

    def test_request_draw_builds_between_begin_write_and_publish(self):
        body = self.function_body(
            self.ino(), "void requestDraw(micropad::DrawReason reason)")
        begin_at = body.index("beginWrite()")
        build_at = body.index("buildSnapshot(")
        publish_at = body.index("publish(")
        self.assertLess(begin_at, build_at)
        self.assertLess(build_at, publish_at)

    def test_request_draw_builds_outside_critical_section(self):
        # buildSnapshot reads application state and fills the slot; the
        # critical sections only bracket the two mailbox metadata operations.
        # The fill must sit between them, never inside
        # portENTER_CRITICAL/portEXIT_CRITICAL.
        body = self.function_body(
            self.ino(), "void requestDraw(micropad::DrawReason reason)")
        build_at = body.index("buildSnapshot(")
        before = body[:build_at]
        after = body[build_at:]
        for critical in ("portENTER_CRITICAL", "portEXIT_CRITICAL"):
            # exactly one open/close pair around beginWrite ... one pair
            # around publish, and none at the fill position
            self.assertEqual(before.count(critical), 1)
            self.assertEqual(after.count(critical), 1)

    def test_render_task_waits_on_notification_not_polling(self):
        body = self.function_body(self.ino(), "void renderTask(void *)")
        # The rendered task is WDT-subscribed, so the wait must be bounded and
        # the task must pet the watchdog while idle; it is a notification wait
        # with a timeout, never a busy-poll, never a blocking delay().
        self.assertIn("ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(200))", body)
        self.assertIn("esp_task_wdt_reset()", body)
        self.assertNotIn("ulTaskNotifyTake(pdTRUE, portMAX_DELAY)", body)
        self.assertNotIn("delay(", body)
        self.assertNotIn("Serial", body)
        self.assertNotIn("println", body)

    def test_render_task_is_the_only_call_site_of_draw_snapshot(self):
        # drawSnapshot is the exclusive Core 0 display seam; the definition
        # plus the single render-task call are the only occurrences in the
        # whole sketch.
        text = self.ino()
        self.assertEqual(text.count("drawSnapshot("), 2)
        self.assertEqual(
            text.count("drawSnapshot(snapshotMailbox.readable(slot))"), 1)
        render_task_at = text.index("void renderTask(void *)")
        call_at = text.index("drawSnapshot(snapshotMailbox.readable(slot))")
        self.assertLess(render_task_at, call_at)

    def test_render_task_sets_and_clears_render_busy(self):
        body = self.function_body(self.ino(), "void renderTask(void *)")
        self.assertIn("renderBusy.store(true, std::memory_order_release)", body)
        self.assertIn("renderBusy.store(false, std::memory_order_release)", body)

    def test_renderer_claims_once_and_pull_loops(self):
        # After one claim/release, the renderer looks once for a newer
        # published generation before blocking again (no polling loop).
        body = self.function_body(self.ino(), "void renderTask(void *)")
        self.assertLess(body.index("claimNewest()"),
                        body.index("drawSnapshot("))
        self.assertIn("snapshotMailbox.release(", body)
        self.assertNotIn("while (", body)

    def test_build_snapshot_side_effects_and_immutability(self):
        # The snapshot carries copied strings/scalars only, and the page-derived
        # part is built by the core (fillPageSnapshot), so the panel and the host
        # preview cannot disagree about the row window.
        body = self.function_body(
            self.ino(), "void buildSnapshot(micropad::RenderSnapshot &out)")
        self.assertLess(body.index("out.generation ="),
                        body.index("safeCopy(out.title,"))
        self.assertIn("micropad::fillPageSnapshot(*page, pad.state, out)", body)
        self.assertNotIn("&out.rows", body)

        core = self.source("firmware/micropad_core.cpp")
        self.assertIn("void fillPageSnapshot(const Page &page, const AppState &state,",
                      core)
        self.assertIn("  safeCopy(out.title, page.title);", core)
        self.assertIn("  for (uint8_t i = 0; i < static_cast<uint8_t>(VISIBLE_ROWS); ++i) {", core)
        for field in ("name", "state", "unit"):
            self.assertIn(f"    safeCopy(row.{field}, item.{field});", core)
        for field in ("value", "selected", "editing"):
            self.assertIn(f"    row.{field} =", core)


class FirmwareDisplayContractTest(FirmwareStaticContractTest):
    """Full-panel landscape rendering contract (Task 9).

    The display physics are non-negotiable: exactly one partial window (the
    full native 128x296 panel window), ``display.init(0)``, disabled
    diagnostics, and no serial output on the render/refresh path. The layout
    geometry itself is proven by the host binary (``test_core.cpp``); these
    checks pin the sketch-side display adapters and the named layout constants
    in the core header.
    """

    def ino(self) -> str:
        return self.source("firmware/MicroPad_HA_Controller.ino")

    def core_header(self) -> str:
        return self.source("firmware/micropad_core.h")

    # --- Step 1: the panel class and full-window rules ---

    def test_exact_display_template_and_initialization(self):
        text = self.ino()
        self.assertIn(
            "GxEPD2_BW<GxEPD2_290_T94_V2, GxEPD2_290_T94_V2::HEIGHT> display(",
            text,
        )
        body = self.function_body(text, "void initDisplay()")
        self.assertIn("SPI.begin(PIN_EPD_SCK, -1, PIN_EPD_MOSI, -1)", body)
        self.assertLess(body.index("SPI.begin(PIN_EPD_SCK, -1, PIN_EPD_MOSI, -1)"), body.index("display.init(0)"))
        self.assertIn("display.init(0)", body)
        self.assertIn("display.setRotation(1)", body)
        self.assertIn("GxEPD_BLACK", body)
        self.assertNotIn("Serial", body)
        self.assertNotIn("delay(", body)

    def test_only_full_panel_partial_windows(self):
        # Exactly one partial-window call in the whole sketch, always the full
        # native panel window; per-row band windows are prohibited.
        text = self.ino()
        self.assertEqual(text.count("setPartialWindow("), 1)
        self.assertIn("setPartialWindow(0, 0, 128, 296)", text)

    def test_full_vs_partial_window_selection(self):
        body = self.function_body(self.ino(), "void drawSnapshot(")
        self.assertIn("setFullWindow()", body)
        self.assertIn("firstPage()", body)
        self.assertIn("nextPage()", body)

    # --- Step 2: refresh policy ---

    def test_forced_full_reasons_reach_full(self):
        # Boot, portal enter/exit, BUSY recovery, and the partial limit all
        # reach a forced full refresh (behavior proven in the host binary).
        self.assertIn("refreshMustBeFull(", self.core_header())
        self.assertIn("constexpr uint8_t MAX_PARTIAL_REFRESHES = 0;",
                      self.core_header())
        body = self.function_body(self.ino(), "void requestDraw(")
        self.assertIn("micropad::refreshMustBeFull(", body)

    def test_partial_spacing_and_counting_policy(self):
        # A partial start checks wrap-safe elapsed time against
        # MIN_REFRESH_SPACING_MS; the counter resets only after a full refresh
        # and increments only after a successful partial refresh.
        core = self.core_header()
        self.assertIn("constexpr uint32_t MIN_REFRESH_SPACING_MS = 100;", core)
        self.assertIn("constexpr uint8_t MAX_PARTIAL_REFRESHES = 0;", core)
        self.assertIn("class RefreshPolicy", core)
        self.assertIn("remainingSpacingMs(", core)
        self.assertIn("beginRefresh(", core)
        self.assertIn("completeRefresh(", core)
        self.assertIn("partialCount(", core)
        body = self.function_body(self.ino(), "void waitForRefreshSpacing()")
        self.assertIn("remainingSpacingMs(", body)
        self.assertIn("MAX_PARTIAL_REFRESHES", body)
        self.assertNotIn("Serial", body)

    def test_busy_recovery_forces_full_reenable(self):
        text = self.ino()
        body = self.function_body(text, "bool waitForPanelReady()")
        self.assertIn("PIN_EPD_BUSY", body)
        self.assertIn("DISPLAY_BUSY_TIMEOUT_MS", body)
        self.assertNotIn("Serial", body)
        self.assertNotIn("println", body)
        recover = self.function_body(text, "void recoverDisplay()")
        # Recovery re-runs the display initialization on Core 0 (initDisplay
        # itself performs display.init(0), the full native window and the
        # landscape rotation) and forces the next refresh through the full
        # path.
        self.assertIn("initDisplay()", recover)
        self.assertIn("requireRecoveryFull()", recover)
        self.assertIn("recoverDisplay()",
                      self.function_body(text, "void drawSnapshot("))

    # --- Step 3: every UI region ---

    def test_landscape_layout_constants(self):
        core = self.core_header()
        self.assertIn("constexpr int16_t CANVAS_WIDTH = 296;", core)
        self.assertIn("constexpr int16_t CANVAS_HEIGHT = 128;", core)
        self.assertIn("constexpr int16_t TITLE_STRIP_HEIGHT = 24;", core)
        self.assertIn("constexpr int16_t ROW_HEIGHT = 26;", core)
        self.assertIn("constexpr int16_t STATUS_CELL_X = 276;", core)
        self.assertIn("constexpr int16_t STATUS_CELL_W = 20;", core)
        self.assertIn("constexpr int16_t POWER_SLOT_X = 256;", core)
        self.assertIn("constexpr int16_t POWER_SLOT_W = 20;", core)
        self.assertIn("constexpr int16_t SCROLLBAR_X = 291;", core)
        self.assertIn("constexpr size_t VISIBLE_ROWS = 4;", core)

    def test_ui_functions_delegate_geometry_to_core(self):
        # The sketch-side UI seams translate core-built geometry; the actual
        # layout rules are host-tested in the prim model.
        text = self.ino()
        self.assertIn("void drawNormalUi(", text)
        self.assertIn("void drawPortalUi(", text)
        self.assertIn("void drawNetworkStatus(", text)
        self.assertIn("void drawPowerSymbol(", text)
        self.assertIn("micropad::layoutNormalUi(",
                      self.function_body(text, "void drawNormalUi("))
        self.assertIn("micropad::layoutPortalUi(",
                      self.function_body(text, "void drawPortalUi("))
        self.assertIn("micropad::networkStatusPrims(",
                      self.function_body(text, "void drawNetworkStatus("))
        self.assertIn("micropad::powerIconPrims(",
                      self.function_body(text, "void drawPowerSymbol("))

    def test_draw_power_symbol_geometry_only(self):
        # The power symbol is pure line/rectangle/circle geometry: no text or
        # glyph call anywhere in the seam.
        body = self.function_body(self.ino(), "void drawPowerSymbol(")
        self.assertNotIn("print(", body)
        self.assertNotIn("setFont(", body)
        self.assertNotIn("setCursor(", body)
        self.assertNotIn("drawChar", body)
        self.assertNotIn("glyph", body)

    def test_render_path_never_prints(self):
        # Serial over USB-CDC blocks forever with no host attached (historic
        # freeze root cause): the entire render/refresh path stays silent.
        text = self.ino()
        for signature in ("void drawSnapshot(", "void initDisplay()",
                          "bool waitForPanelReady()",
                          "void waitForRefreshSpacing()",
                          "void recoverDisplay()"):
            body = self.function_body(text, signature)
            self.assertNotIn("Serial", body, signature)
            self.assertNotIn("println", body, signature)
            self.assertNotIn("printf", body, signature)


class FirmwarePowerContractTest(FirmwareStaticContractTest):
    """Debounce-guarded USB host detection and power state (Task 10).

    The stable-window debounce state machine (``PowerDebouncer``, 500 ms
    sample interval / 1500 ms stable window) is host-tested in
    ``test_core.cpp``; these checks pin the sketch-side adapters: the
    CDC-on-boot shielded power probe (data lines only, never a VBUS/charger
    GPIO guess), the additional ``ARDUINO_USB_MODE == 1`` guard on the
    HWCDC-only method, and ``powerTick``'s edge-only effects (draw + retained
    publish only on debounced edges, never unconditionally from a loop path).
    """

    def ino(self) -> str:
        return self.source("firmware/MicroPad_HA_Controller.ino")

    def test_is_powered_shielded_cdc_only(self):
        body = self.function_body(self.ino(), "bool isPowered()")
        # The Serial boolean conversion is enclosed by the CDC-on-boot guard;
        # the #else arm returns false so CDC-disabled builds report unpowered.
        guard_at = body.index(
            "#if defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT")
        serial_at = body.index("static_cast<bool>(Serial)")
        else_at = body.index("#else")
        false_at = body.index("return false;")
        endif_at = body.index("#endif")
        self.assertLess(guard_at, serial_at)
        self.assertLess(serial_at, else_at)
        self.assertLess(else_at, false_at)
        self.assertLess(false_at, endif_at)
        # The #else arm never touches Serial.
        self.assertNotIn("Serial", body[else_at:endif_at])
        # Data-line detection only: no VBUS/charger GPIO guess anywhere.
        for token in ("VBUS", "vbus", "analogRead", "digitalRead",
                      "digitalWrite", "pinMode", "gpio", "charger"):
            self.assertNotIn(token, body)

    def test_configure_cdc_guards_hwcdc_timeout(self):
        body = self.function_body(self.ino(), "void configureCdc()")
        # setTxTimeoutMs is a HWCDC method: guarded by both the CDC-on-boot
        # flag and ARDUINO_USB_MODE == 1, so TinyUSB and CDC-disabled builds
        # never compile an unavailable call.
        guard_at = body.index(
            "#if defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT &&")
        mode_at = body.index("ARDUINO_USB_MODE == 1")
        call_at = body.index("Serial.setTxTimeoutMs(0)")
        self.assertLess(guard_at, mode_at)
        self.assertLess(mode_at, call_at)
        self.assertEqual(body.count("setTxTimeoutMs"), 1)

    def test_power_tick_interval_gate_and_edge_only_effects(self):
        body = self.function_body(self.ino(), "void powerTick(uint32_t nowMs)")
        # Sample no more often than every 500 ms: the early return precedes
        # the sample and every side effect.
        self.assertIn("micropad::USB_SAMPLE_MS", body)
        self.assertLess(body.index("return;"),
                        body.index("powerDebouncer.sample("))
        # requestDraw and the retained publish exist exactly once per
        # debounced-edge branch and never outside them: PowerEdge::None makes
        # neither call, and no raw sample is ever published.
        self.assertEqual(body.count("requestDraw("), 2)
        self.assertEqual(body.count("publishPowerStateRetained("), 2)
        connected_at = body.index("PowerEdge::Connected")
        disconnected_at = body.index("PowerEdge::Disconnected")
        self.assertLess(connected_at, body.index("requestDraw("))
        self.assertLess(disconnected_at, body.rindex("requestDraw("))
        self.assertLess(connected_at, body.index("publishPowerStateRetained("))
        self.assertLess(disconnected_at,
                        body.rindex("publishPowerStateRetained("))
        # The payload is the debounced edge value; the stable shared state is
        # updated on the same edge so snapshots re-render the power icon.
        self.assertIn("publishPowerStateRetained(true)", body)
        self.assertIn("publishPowerStateRetained(false)", body)
        self.assertIn("stableUsbHost = true", body)
        self.assertIn("stableUsbHost = false", body)
        self.assertNotIn("publishPowerStateRetained(isPowered", body)
        # Hot path stays nonblocking and silent.
        for token in ("delay(", "while (", "Serial", "printf"):
            self.assertNotIn(token, body)

    def test_power_state_flows_into_snapshots(self):
        # The top-right power icon renders from the stable host state: every
        # snapshot copies stableUsbHost into usbHost (F8 builder), and the
        # F9 draw seams render powerIconPrims from snap.usbHost whenever the
        # snapshot says a USB host is present.
        body = self.function_body(
            self.ino(), "void buildSnapshot(micropad::RenderSnapshot &out)")
        self.assertIn("out.usbHost = stableUsbHost;", body)
        for signature in ("void drawNormalUi(", "void drawPortalUi("):
            seam = self.function_body(self.ino(), signature)
            self.assertIn("if (snap.usbHost)", seam)
            self.assertIn("drawPowerSymbol(", seam)

    def test_draw_seams_append_to_single_prim_model(self):
        # The status cell and power icon append to the caller's prim model
        # (single 2.4 KB RenderModel on the render task stack). Two nested
        # models plus GxEPD2 frames overflowed the former 6144-byte stack and
        # panicked mid-draw (blank panel / reboot, issue #6).
        text = self.ino()
        # Both seams take the caller's model in the signature (the body alone
        # cannot show the parameter).
        self.assertIn(
            "void drawNetworkStatus(uint8_t networkState, "
            "micropad::RenderModel &model)", text)
        self.assertIn(
            "void drawPowerSymbol(bool usbHost, micropad::RenderModel &model)",
            text)
        for signature in ("void drawNetworkStatus(", "void drawPowerSymbol("):
            seam = self.function_body(text, signature)
            # The seam appends prims but never draws or owns a model itself.
            self.assertIn("model)", seam)
            self.assertNotIn("drawRenderModel(", seam)
            self.assertNotIn("RenderModel model;", seam)
        for signature in ("void drawNormalUi(", "void drawPortalUi("):
            body = self.function_body(text, signature)
            self.assertEqual(body.count("RenderModel model;"), 1)
            self.assertEqual(body.count("drawRenderModel(model)"), 1)

    def test_power_publish_retained_and_republished_on_connect(self):
        body = self.function_body(
            self.ino(), "bool publishPowerStateRetained(bool usbHost)")
        self.assertIn('doc["usb_host"] = usbHost;', body)
        self.assertIn("publish(mp::TOPIC_POWER, buffer, true)", body)
        # The retained publication reports its own failure so a dropped power
        # state is repaired by the next reconnect instead of being ignored.
        self.assertIn("return mqttClient.publish", body)
        # Every successful MQTT connection republishes the current stable
        # state independently (retained subscribers get the truth on boot).
        self.assertIn(
            "publishPowerStateRetained(stableUsbHost)",
            self.function_body(self.ino(), "void publishInitialRequests()"))


class FirmwareSleepContractTest(FirmwareStaticContractTest):
    """Warm light sleep entry and recovery (Task 11).

    The dependency-free sleep predicate (``canSleep``: exactly 60000 ms idle
    and 3000 ms since ``awakeStartedMs``, each of the four blockers) is
    host-tested in ``test_core.cpp``; these checks pin the sketch-side dual
    power guards, the pre-sleep wake-input/render-suspend ordering, and the
    post-wake restore sequence in ``MicroPad_HA_Controller.ino``.
    """

    def ino(self) -> str:
        return self.source("firmware/MicroPad_HA_Controller.ino")

    def test_sleep_platform_includes_present(self):
        text = self.ino()
        for header in ("<esp_sleep.h>", "<esp_task_wdt.h>",
                       "<esp_timer.h>"):
            self.assertIn(header, text)

    def test_dual_independent_power_guards(self):
        # First guard: the caller-side gate carries its own isPowered() check
        # before any sleep operation and feeds every blocker into canSleep(),
        # whose host tests enforce the 60000 ms idle and 3000 ms awake rules.
        gate = self.function_body(
            self.ino(), "bool sleepGateReady(uint32_t nowMs)")
        self.assertLess(gate.index("isPowered()"), gate.index("canSleep("))
        for token in ("anyKeyHeld()",
                      "renderBusy.load(std::memory_order_acquire)",
                      "snapshotMailbox.hasPending()", "lastActivityMs",
                      "awakeStartedMs", "nowMs"):
            self.assertIn(token, gate)
        # Second guard: enterLightSleep begins with the power return before
        # any sleep operation.
        body = self.function_body(
            self.ino(), "void enterLightSleep(uint32_t nowMs)")
        self.assertTrue(body.lstrip().startswith("if (isPowered()) return;"))

    def test_portal_blocks_sleep_at_both_guards(self):
        # The setup portal must never light-sleep (issue #6): light sleep
        # silences the AP, DNS and web server mid-setup, so the pairing phone
        # loses the pad and the session looks like a crash. The portal instead
        # restarts on its own PORTAL_IDLE_MS inactivity timeout.
        gate = self.function_body(
            self.ino(), "bool sleepGateReady(uint32_t nowMs)")
        portal_at = gate.index("portalActive")
        self.assertLess(gate.index("isPowered()"), portal_at)
        self.assertLess(portal_at, gate.index("canSleep("))
        body = self.function_body(
            self.ino(), "void enterLightSleep(uint32_t nowMs)")
        self.assertLess(body.index("portalActive"),
                        body.index("prepareWakeInputs("))

    def test_pre_sleep_wake_input_configuration(self):
        body = self.function_body(self.ino(), "void prepareWakeInputs()")
        # All rows are driven LOW so a pressed matrix key pulls a column LOW.
        self.assertIn("digitalWrite(ROW_PINS[row], LOW)", body)
        # Columns and encoder A/B are configured INPUT_PULLUP wake candidates.
        self.assertIn("pinMode(COL_PINS[col], INPUT_PULLUP)", body)
        self.assertIn("pinMode(ENCODER_A_PIN, INPUT_PULLUP)", body)
        self.assertIn("pinMode(ENCODER_B_PIN, INPUT_PULLUP)", body)
        # Proven v6 wake mechanism: per-pin GPIO level wakeup (not ext1, which
        # leaves the S3 pins in RTC mode and produced phantom matrix reads after
        # wake), covering all four column pins plus the two encoder pins.
        self.assertIn("esp_sleep_enable_gpio_wakeup()", body)
        self.assertIn("gpio_wakeup_enable(static_cast<gpio_num_t>(COL_PINS[col])", body)
        self.assertIn("GPIO_INTR_LOW_LEVEL", body)
        self.assertIn("gpio_wakeup_enable(static_cast<gpio_num_t>(ENCODER_A_PIN)", body)
        self.assertIn("gpio_wakeup_enable(static_cast<gpio_num_t>(ENCODER_B_PIN)", body)
        self.assertNotIn("esp_sleep_enable_ext1_wakeup(", body)
        self.assertNotIn("delay(", body)
        self.assertNotIn("Serial", body)

    def test_pre_sleep_render_suspend_and_modem_sleep_order(self):
        body = self.function_body(
            self.ino(), "void enterLightSleep(uint32_t nowMs)")
        # Render task suspension precedes the loop task leaving the watchdog,
        # which precedes the sleep start; the timer is recorded immediately
        # before and after esp_light_sleep_start().
        self.assertLess(body.index("vTaskSuspend("),
                        body.index("esp_task_wdt_delete(NULL)"))
        self.assertLess(body.index("esp_task_wdt_delete(NULL)"),
                        body.index("esp_light_sleep_start("))
        self.assertLess(body.index("esp_timer_get_time()"),
                        body.index("esp_light_sleep_start("))
        self.assertLess(body.index("esp_light_sleep_start("),
                        body.rindex("esp_timer_get_time()"))
        # Modem sleep is enabled with the Wi-Fi station association intact.
        self.assertIn("WiFi.setSleep(true)", body)
        self.assertNotIn("WiFi.disconnect", body)
        # No display, Serial, MQTT publish, or Preferences call between the
        # second guard and the sleep start.
        for token in ("Serial", "display.", "publish", "Preferences"):
            self.assertNotIn(token, body)

    def test_wake_restore_order_mqtt_and_brake(self):
        body = self.function_body(
            self.ino(), "void enterLightSleep(uint32_t nowMs)")
        # The full wake path runs before enterLightSleep returns: restore,
        # the 3000 ms wake-loop brake, then the immediate wake input.
        self.assertIn("processWakeInput(", body)
        self.assertLess(body.index("restoreAfterWake("),
                        body.index("processWakeInput("))
        self.assertLess(body.index("awakeStartedMs = wakeMs"),
                        body.index("processWakeInput("))
        # The idle clock restarts at wake: without it the pad re-slept after
        # ~3 s and its LOW-driven matrix rows read one press in every row.
        self.assertLess(body.index("lastActivityMs = wakeMs"),
                        body.index("processWakeInput("))
        restore = self.function_body(
            self.ino(),
            "void restoreAfterWake(uint32_t wakeMs, uint64_t sleptUs)")
        # Resume precedes watchdog re-arm (the loop task is the subscriber
        # and is re-added after every wake).
        self.assertLess(restore.index("vTaskResume("),
                        restore.index("esp_task_wdt_add(NULL)"))
        self.assertIn("esp_task_wdt_add(NULL)", restore)
        # All matrix rows return to their HIGH idle state.
        self.assertIn("digitalWrite(ROW_PINS[row], HIGH)", restore)
        # A sleep longer than the 15 s MQTT keepalive invalidates the likely
        # stale socket and schedules an immediate reconnect.
        self.assertLess(restore.index("sleptUs"),
                        restore.index("mqttNetworkClient.stop()"))
        self.assertIn("mqttNextAttemptMs = wakeMs", restore)
        # Debounce re-prime precedes the immediate wake input processing; the
        # encoder phase is re-baselined (reset) rather than fed as a step so a
        # wake-time sample cannot emit a phantom scroll (hardware-observed).
        self.assertIn("primeForWake(", restore)
        self.assertIn("encoderDecoder.reset(phase)", restore)
        self.assertIn("digitalRead(ENCODER_A_PIN", restore)
        # Back to full speed after the wake: the pre-sleep modem sleep is
        # turned off again (proven v6 behaviour) and the per-pin GPIO wake
        # configuration is disabled so the matrix pins return to normal GPIO.
        self.assertIn("WiFi.setSleep(false)", restore)
        self.assertIn("esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_GPIO)", restore)
        # The input pins are fully re-established after light sleep: a partially
        # configured matrix read phantom keys (one press became two actions).
        self.assertIn("setupInputPins()", restore)
        # Nothing in the wake path tears down Wi-Fi association or prints.
        self.assertNotIn("WiFi.disconnect", restore)
        self.assertNotIn("Serial", restore)

    def test_wake_input_emitted_immediately(self):
        body = self.function_body(
            self.ino(), "void processWakeInput(uint32_t wakeMs)")
        # The waking matrix press becomes an action on the immediate scan:
        # a fresh active-low pass emitted through the single input seam.
        self.assertIn("readMatrixKey(", body)
        self.assertIn("emitInput(", body)
        self.assertIn("wakeMs", body)
        # Only a genuine external wake processes input; a sleep that returned
        # immediately (undefined cause) must not emit phantom actions, and an
        # unsettled scan emits at most one press.
        self.assertIn("ESP_SLEEP_WAKEUP_UNDEFINED", body)
        self.assertIn("return;", body)
        self.assertNotIn("delay(", body)
        self.assertNotIn("while (", body)


class FirmwareLoopContractTest(FirmwareStaticContractTest):
    """Integrated Core 1 loop and setup wiring (Task 12).

    These checks pin the exact cooperative ``loop()`` order and the
    ``setup()`` initialization order (CDC guards -> input pins -> default
    keymap -> Preferences -> MQTT config -> display -> render task -> boot
    refresh -> first-boot portal vs station), plus the cross-subsystem
    wiring carried in from F6/F8/F10/F11: MQTT paths stop at portal entry,
    ``createRenderTask()`` precedes every ``requestDraw``, the activity and
    USB-sample clocks are seeded at boot, the render task is watchdog
    registered in setup, and the recovery draw request is gated.
    """

    def ino(self) -> str:
        return self.source("firmware/MicroPad_HA_Controller.ino")

    def test_loop_exact_cooperative_order(self):
        body = self.function_body(self.ino(), "void loop()")
        tokens = (
            "esp_task_wdt_reset();  // feed the loop task's watchdog subscription",
            "scanMatrix(nowMs)",
            "scanEncoder(nowMs)",
            "portalTick(nowMs)",
            "wifiTick(nowMs)",
            "mqttTick(nowMs)",
            "if (mqttClient.connected()) mqttClient.loop();",
            "flushEventQueue();",
            "resyncTick(nowMs)",
            "catalogRequestTick(nowMs)",
            "powerTick(nowMs)",
            "renderRecoveryTick(nowMs)",
            "LIGHT_SLEEP_ENABLED && sleepGateReady(nowMs)",
        )
        pos = -1
        for token in tokens:
            at = body.index(token)
            self.assertGreater(at, pos, f"loop order broken at {token}")
            self.assertEqual(body.count(token), 1,
                             f"{token} must occur exactly once")
            pos = at
        # The loop never blocks, never touches the display, and never requests
        # a redraw: draw requests originate only inside the tick functions on
        # explicit state-change edges (brief step 5).
        for banned in ("while ", "delay(", "display.", "requestDraw(",
                       "Serial", "println", "printf"):
            self.assertNotIn(banned, body, f"loop must not contain {banned}")

    def test_setup_hardware_then_network_then_render_order(self):
        body = self.function_body(self.ino(), "void setup()")
        order = ("configureCdc()", "setupInputPins()", "loadDefaultKeymap(",
                 "loadSettings(", "mqttClient.setBufferSize(",
                 "mqttClient.setServer(deviceSettings.mqttHost, deviceSettings.mqttPort)",
                 "initDisplay()", "createRenderTask()",
                 "esp_task_wdt_add(NULL)")
        pos = -1
        for token in order:
            at = body.index(token)
            self.assertGreater(at, pos, f"setup order broken at {token}")
            pos = at
        self.assertNotIn("requestDraw(", body)
        self.assertLess(body.index("createRenderTask()"),
                        body.index("startPortal("))

    def test_setup_first_boot_portal_else_station_async(self):
        body = self.function_body(self.ino(), "void setup()")
        # Settings absent -> first-boot portal; present -> station mode plus
        # one asynchronous Wi-Fi attempt, never a wait for a connection.
        self.assertLess(body.index("startPortal("),
                        body.index("WiFi.mode(WIFI_STA)"))
        self.assertLess(body.index("WiFi.mode(WIFI_STA)"),
                        body.index("WiFi.begin(deviceSettings.wifiSsid,"))
        self.assertNotIn("while ", body)
        self.assertNotIn("WL_CONNECTED", body)

    def test_setup_seeds_activity_and_usb_sample_clocks(self):
        # F11 carry: awakeStartedMs seeded at boot anchors the 3000 ms
        # post-wake brake from the boot instant. F10 carry: lastUsbSampleMs
        # seeded so the first powerTick waits one full 500 ms interval.
        body = self.function_body(self.ino(), "void setup()")
        self.assertIn("awakeStartedMs = millis()", body)
        self.assertIn("lastUsbSampleMs = millis()", body)

    def test_setup_core_pin_check_debug_only(self):
        body = self.function_body(self.ino(), "void setup()")
        # xPortGetCoreID() == 1 is asserted only inside #if MICROPAD_DEBUG
        # (compiled out of release builds) and setup never prints.
        self.assertLess(body.index("#if MICROPAD_DEBUG"),
                        body.index("xPortGetCoreID()"))
        for token in ("Serial", "println", "printf"):
            self.assertNotIn(token, body)

    def test_setup_configures_task_wdt_budget(self):
        # Issue #6: the loop task is the TWDT subscriber, but the setup
        # portal's WebServer can block the loop for its full socket timeouts
        # while a pairing phone stalls a request; the IDF default deadline is
        # 5 s with panic, so a single slow portal page panics and reboots the
        # pad. setup() must configure the v6-proven 20 s budget (with the
        # reconfigure fallback when the IDF already initialised the TWDT).
        text = self.ino()
        self.assertIn(
            "constexpr uint32_t TASK_WDT_TIMEOUT_MS = 20000;",
            self.source("firmware/micropad_core.h"))
        body = self.function_body(text, "void setup()")
        self.assertIn("esp_task_wdt_config_t wdtCfg = {};", body)
        self.assertIn("wdtCfg.timeout_ms = micropad::TASK_WDT_TIMEOUT_MS;", body)
        self.assertIn("wdtCfg.idle_core_mask = 0;", body)
        self.assertIn("wdtCfg.trigger_panic = true;", body)
        self.assertIn("esp_task_wdt_init(&wdtCfg)", body)
        self.assertIn("esp_task_wdt_reconfigure(&wdtCfg)", body)
        # The budget is configured before the loop task subscribes.
        self.assertLess(body.index("esp_task_wdt_init(&wdtCfg)"),
                        body.index("esp_task_wdt_add(NULL)"))

    def test_render_recovery_tick_gated_draw(self):
        body = self.function_body(self.ino(),
                                  "void renderRecoveryTick(uint32_t nowMs)")
        # The recovery redraw request is gated on the Core 0 flag and the flag
        # is consumed exactly once per recovery event: never an unconditional
        # draw from a loop path.
        self.assertIn("recoveryDrawNeeded.exchange(false", body)
        self.assertIn("std::memory_order_acquire", body)
        self.assertIn("micropad::DrawReason::Recovery", body)
        self.assertEqual(body.count("requestDraw("), 1)
        for banned in ("while ", "delay(", "display.", "Serial", "println",
                       "printf"):
            self.assertNotIn(banned, body)

    def test_mqtt_paths_stop_at_portal_entry(self):
        # Task 6 contract "stop MQTT attempts at portal entry" is honored on
        # every MQTT-touching loop path now that the loop exists.
        text = self.ino()
        for signature in ("void wifiTick(uint32_t nowMs)",
                          "void mqttTick(uint32_t nowMs)",
                          "void resyncTick(uint32_t nowMs)",
                          "void catalogRequestTick(uint32_t nowMs)",
                          "void flushEventQueue()"):
            body = self.function_body(text, signature)
            self.assertIn("portalActive", body, signature)

    def test_get_all_pages_reserved_for_first_mqtt_connection(self):
        body = self.function_body(self.ino(), "void publishInitialRequests()")
        # get_all_pages is requested exactly once per boot, only before the
        # first-connection flag is set; every later (re)connect asks the
        # current authoritative page instead.
        self.assertEqual(body.count("GetAllPages"), 1)
        self.assertLess(body.index("GetAllPages"),
                        body.index("firstMqttConnectionThisBoot = true"))
        self.assertIn("queueNavigateRequest(", body)


class FirmwareReleaseAssertionTest(FirmwareStaticContractTest):
    """Final firmware release assertions (Task 14, Steps 1a-1d).

    These close the acceptance contract: the full set of fourteen bindable
    physical input IDs, the exact MQTT/queue bounds, the two fixed render
    slots and USB debounce window, and the diffuse AI-assisted notice carried
    by every firmware, test, script, workflow, config, .gitignore, and
    firmware-document file in the exact comment/block-quote form the plan's
    Global Constraints prescribe.
    """

    NOTICE = ("AI-assisted development \u2014 firmware, HA automation, config "
              "generator and docs were created with AI (LLM) help, reviewed "
              "and tested by the author. Provided as-is, without warranty; "
              "verify on your own hardware, don't use for safety-critical "
              "applications.")

    def ino(self) -> str:
        return self.source("firmware/MicroPad_HA_Controller.ino")

    def core_header(self) -> str:
        return self.source("firmware/micropad_core.h")

    def core_impl(self) -> str:
        return self.source("firmware/micropad_core.cpp")

    # --- Step 1a: pins, active-low restoration, and all 14 inputs ---

    def test_all_fourteen_input_ids_are_bindable(self):
        # The 14 physical inputs are r0c0..r2c3 (12 matrix switches) plus the
        # two encoder detent directions. All fourteen must be representable in
        # the key-id table, covered by the fixed keymap, and the shared
        # constant must equal the physical count.
        self.assertIn("constexpr size_t KEY_COUNT = 14;", self.core_header())
        self.assertIn("constexpr uint8_t MATRIX_KEY_COUNT = "
                      "MATRIX_ROWS * MATRIX_COLS;", self.core_header())
        self.assertEqual(12, 3 * 4)  # MATRIX_KEY_COUNT == 12 matrix switches
        self.assertIn("constexpr size_t KEY_COUNT = 14",
                      self.core_header())
        # The exact source table holds r0c0..r2c3 then enc_up then enc_down.
        table = self.core_impl()
        self.assertIn(
            '"r0c0", "r0c1", "r0c2", "r0c3", "r1c0", "r1c1", "r1c2",',
            table)
        self.assertIn(
            '"r1c3", "r2c0", "r2c1", "r2c2", "r2c3", "enc_up", "enc_down"}',
            table)
        # The whole-set name table is sized KEY_COUNT and the default keymap
        # fills every slot.
        self.assertIn("constexpr const char *kKeyNames[KEY_COUNT] =",
                      table)

    # --- Step 1b: bounded MQTT buffering and queueing ---

    def test_mqtt_buffer_exact_and_queue_bounds(self):
        # The 16384-byte buffer is the pipeline width; the event queue is
        # exactly 16 slots and the flush cap is four per loop pass.
        self.assertIn("constexpr size_t MQTT_BUFFER_BYTES = 16384;",
                      self.core_header())
        self.assertIn("constexpr size_t QUEUE_CAPACITY = 16;",
                      self.core_header())
        self.assertIn("constexpr size_t MAX_FLUSH_PER_LOOP = 4;",
                      self.core_header())
        self.assertIn("Event slots_[QUEUE_CAPACITY];", self.core_header())

    def test_mqtt_subscriptions_exact(self):
        # Exactly the three required subscriptions, issued once each on every
        # connect, with no extra topic and no orphan subscribe call.
        text = self.ino()
        self.assertEqual(text.count("mqttClient.subscribe("), 3)
        self.assertIn("mqttClient.subscribe(mp::TOPIC_PAGES);", text)
        self.assertIn("mqttClient.subscribe(mp::TOPIC_CURRENT_PAGE);", text)
        self.assertIn("mqttClient.subscribe(mp::TOPIC_KEYMAP);", text)
        body = self.function_body(self.ino(), "void mqttTick(uint32_t nowMs)")
        for sig in ("mqttClient.subscribe(mp::TOPIC_PAGES);",
                    "mqttClient.subscribe(mp::TOPIC_CURRENT_PAGE);",
                    "mqttClient.subscribe(mp::TOPIC_KEYMAP);"):
            self.assertIn(sig, body)

    def test_boot_waits_for_catalog_and_starts_on_home(self):
        text = self.ino()
        setup = self.function_body(text, "void setup()")
        self.assertIn('safeCopy(pad.state.pageId, "home")', setup)
        self.assertNotIn("requestDraw(micropad::DrawReason::Boot)", setup)
        callback = self.function_body(text, "void onMqttMessage(")
        self.assertIn("displayedPage", callback)
        self.assertIn("catalogReady = true", callback)
        self.assertIn(
            "catalogReady && std::strcmp(pad.state.pageId, page.pageId) == 0",
            callback)
        self.assertIn("requestDraw(micropad::DrawReason::Boot)", callback)
        self.assertIn("pageContentEqual(*cached, page)", callback)
        self.assertIn("displayedPage &&", callback)
        self.assertIn("contentChanged", callback)

    def test_text_is_centered_and_partial_refresh_is_used(self):
        draw_text = self.function_body(self.ino(), "void drawTextPrim(")
        self.assertIn("(prim.h - static_cast<int16_t>(textH)) / 2", draw_text)
        draw = self.function_body(self.ino(), "void drawSnapshot(")
        self.assertIn("if (mode == micropad::RefreshMode::Full)", draw)
        self.assertIn("setFullPanelPartialRefresh()", draw)

    def test_input_draws_optimistic_state_and_first_mqtt_is_immediate(self):
        text = self.ino()
        emit = self.function_body(
            text, "void emitInput(const micropad::InputEvent &input)")
        self.assertIn("const micropad::ApplyResult result", emit)
        self.assertIn("if (result.stateChanged)", emit)
        self.assertIn("requestDraw(micropad::DrawReason::StateChange)", emit)
        self.assertIn(
            "static_cast<uint32_t>(0U - micropad::MQTT_RETRY_MS)", text)

    # --- Step 1c: two render slots and USB debounce window ---

    def test_two_fixed_render_slots(self):
        self.assertIn("static constexpr int8_t SLOT_COUNT = 2;",
                      self.core_header())
        self.assertIn("Slot slots_[SLOT_COUNT];", self.core_header())

    def test_usb_timing_sample_and_stable_window(self):
        # The 500 ms sample / 1500 ms stable-window debounce constants.
        self.assertIn("constexpr uint32_t USB_SAMPLE_MS = 500;",
                      self.core_header())
        self.assertIn("constexpr uint32_t USB_STABLE_MS = 1500;",
                      self.core_header())

    # --- Step 1d: neutral credentials and no hot-path CDC writes ---

    def test_placeholder_credentials_only_not_real(self):
        # The neutral MQTT/user/password placeholders must be the only
        # credentials in core; the sketch stores real ones only via NVS
        # preferences (no literals leak into source).
        core = self.core_header() + "\n" + self.core_impl()
        self.assertIn('"192.168.0.100"', core)
        self.assertIn("1883", core)
        self.assertIn('"micropad"', core)
        self.assertIn('"replace-me"', core)
        # No real/routable broker host and no non-placeholder MQTT user.
        ips = re.findall(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", core)
        for ip in ips:
            octets = [int(o) for o in ip.split(".")]
            if any(o > 255 for o in octets):
                continue
            self.assertEqual(ip, "192.168.0.100", f"unexpected IP {ip}")
        text = self.ino()
        self.assertNotIn('"wss://', text)
        self.assertNotIn("mqtt://", text)

    def test_no_cdc_writes_on_hot_paths(self):
        # Input, refresh-wait, network, queue-flush, resync, power, and sleep
        # hot paths never write to USB CDC (a historic freeze root cause when
        # no host is attached).
        for signature in (
            "void scanMatrix(uint32_t nowMs)",
            "void scanEncoder(uint32_t nowMs)",
            "void flushEventQueue()",
            "void wifiTick(uint32_t nowMs)",
            "void mqttTick(uint32_t nowMs)",
            "void resyncTick(uint32_t nowMs)",
            "void powerTick(uint32_t nowMs)",
            "bool waitForPanelReady()",
            "void waitForRefreshSpacing()",
            "void enterLightSleep(uint32_t nowMs)",
        ):
            body = self.function_body(self.ino(), signature)
            # CDC output blocks forever with no host attached; snprintf into
            # a bounded buffer is host-side and fine. No "Serial" reference
            # (covers Serial.write/println/print), and no bare printf.
            self.assertNotIn("Serial", body, signature)
            bare = re.sub(r"\bsnprintf\b", "", body)
            self.assertNotIn("printf", bare, signature)

    def test_notice_in_every_firmware_related_file(self):
        # Global Constraints: every source, script, workflow, config,
        # .gitignore, and firmware-document file carries the exact notice in
        # its native comment form. C/C++ uses '//', Python/YAML/shell use '#'
        # (shell keeps the shebang first), JSON uses the _ai_assisted_notice
        # key, and markdown firmware docs use the block-quote visible-prose
        # form.
        cxx = ["firmware/MicroPad_HA_Controller.ino",
               "firmware/micropad_core.h", "firmware/micropad_core.cpp",
               "firmware/protocol_contract.h",
               "tests/firmware/test_core.cpp",
               "tests/cpp/test_protocol_contract.cpp"]
        hash_first = ["tests/firmware/test_compile_matrix.py",
                      "tests/firmware/test_firmware_static.py",
                      "tests/firmware/__init__.py",
                      ".github/workflows/firmware.yml", "arduino-cli.yaml",
                      ".gitignore", "deploy/micropad.service",
                      "homeassistant/automation.example.yaml"]
        shell = ["scripts/compile-firmware.sh", "scripts/setup.sh",
                 "scripts/test-firmware-host.sh",
                 "scripts/install-arduino-toolchain.sh",
                 "scripts/test_firmware_contract.sh"]
        json_file = "config.example.json"
        markdown = ["docs/firmware-hardware-validation.md"]

        for rel in cxx:
            first = self.source(rel).splitlines()[0]
            self.assertTrue(first.startswith("// "), rel)
            self.assertIn(self.NOTICE, first, rel)
        for rel in hash_first:
            first = self.source(rel).splitlines()[0]
            self.assertTrue(first.startswith("# "), rel)
            self.assertIn(self.NOTICE, first, rel)
        for rel in shell:
            lines = self.source(rel).splitlines()
            self.assertTrue(lines[0].startswith("#!"
                                                 ), rel)
            self.assertIn(self.NOTICE, lines[1], rel)
        import json
        cfg = json.loads(self.source(json_file))
        self.assertEqual(cfg["_ai_assisted_notice"], self.NOTICE, json_file)
        for rel in markdown:
            text = self.source(rel)
            self.assertIn("> " + self.NOTICE, text, rel)


if __name__ == "__main__":
    unittest.main()


class FirmwareRobustnessFindingsTest(FirmwareStaticContractTest):
    """P1.3 (partial spacing), P1.4 (MQTT buffer allocation), P1.5 (NVS reads).

    The sketch is not host-compiled, so these pin the sketch-side guarantees
    that cannot be exercised on the host: the spacing condition must not be
    disabled by MAX_PARTIAL_REFRESHES == 0, a failed 16 KiB widening must never
    be followed by a connect, and no NVS field may be read without a
    length/size check.
    """

    def ino(self) -> str:
        return self.source("firmware/MicroPad_HA_Controller.ino")

    # --- P1.3 -----------------------------------------------------------------

    def test_partial_spacing_is_not_disabled_by_zero_partial_limit(self):
        body = self.function_body(self.ino(), "void waitForRefreshSpacing()")
        self.assertIn("micropad::MAX_PARTIAL_REFRESHES == 0", body)
        self.assertIn("remainingSpacingMs(", body)
        # The spacing must still be applied for every normal partial refresh.
        self.assertIn("vTaskDelay(", body)
        self.assertNotIn("Serial", body)

    def test_zero_partial_limit_means_no_forced_full_refresh(self):
        policy = self.function_body(self.source("firmware/micropad_core.h"),
                                  "RefreshMode beginRefresh(")
        # The forced-full guard is limited to a positive limit, so a zero limit
        # never forces periodic full refreshes.
        self.assertIn("MAX_PARTIAL_REFRESHES > 0", policy)
        self.assertIn("snapshotForceFull", policy)

    # --- P1.4 -----------------------------------------------------------------

    def test_every_buffer_widening_is_checked(self):
        text = self.ino()
        self.assertEqual(text.count("mqttClient.setBufferSize("), 2)
        setup = self.function_body(text, "void setup()")
        self.assertIn(
            "if (!mqttClient.setBufferSize(micropad::MQTT_BUFFER_BYTES))", setup
        )
        # A failed widening must leave the client unconfigured, and the
        # configured flag may only be set on the success path.
        self.assertIn("mqttClientConfigured = false;", setup)
        self.assertIn("mqttClientConfigured = true;", setup)

    def test_failed_widening_never_connects(self):
        body = self.function_body(self.ino(), "void mqttTick(uint32_t nowMs)")
        self.assertIn(
            "if (!mqttClient.setBufferSize(micropad::MQTT_BUFFER_BYTES))", body
        )
        configure = body[body.index("if (!mqttClientConfigured)") :]
        guard = configure[: configure.index("mqttClientConfigured = true;")]
        # The failure branch returns without reaching the connect site, and the
        # retry stays throttled and non-blocking.
        self.assertIn("return;", guard)
        self.assertIn("mqttNextAttemptMs = nowMs;", guard)
        self.assertNotIn("connect(", guard)
        # mqttTick still has exactly one connect site and never blocks.
        self.assertEqual(body.count("connect("), 1)
        self.assertNotIn("while ", body)
        self.assertNotIn("delay(", body)

    # --- P1.5 -----------------------------------------------------------------

    def test_settings_reads_are_length_and_size_checked(self):
        text = self.ino()
        helper = self.function_body(text, "bool readSettingString(")
        self.assertIn("getBytesLength(", helper)
        self.assertIn("getString(", helper)
        self.assertIn("capacity - 1", helper)
        port_helper = self.function_body(text, "bool readSettingUShort(")
        self.assertIn("getBytesLength(", port_helper)
        self.assertIn("sizeof(uint16_t)", port_helper)

    def test_load_settings_uses_checked_reads_for_every_field(self):
        body = self.function_body(self.ino(), "bool loadSettings()")
        for field in ("wifi_ssid", "wifi_pass", "mqtt_host", "mqtt_user",
                      "mqtt_pass", "client_id"):
            self.assertIn(f'readSettingString(preferences, "{field}"', body)
        self.assertIn('readSettingUShort(preferences, "mqtt_port"', body)
        self.assertIn("fieldsOk", body)
        # No unchecked read may remain: every stored value is length-verified.
        self.assertNotIn('preferences.getString("wifi_ssid"', body)
        self.assertNotIn('preferences.getString("mqtt_host"', body)
        # A corrupt record must not be accepted and nothing may be logged.
        self.assertNotIn("deviceSettings = candidate;", body.split("fieldsOk", 1)[0])
        self.assertNotIn("Serial", body)
        self.assertNotIn("println", body)

    def test_empty_ssid_is_only_valid_in_the_unconfigured_portal_state(self):
        body = self.function_body(self.ino(), "bool loadSettings()")
        # The SSID is a required field, so an empty value fails the read and
        # setup() falls through to the setup portal instead of a broker connect.
        self.assertIn('"wifi_ssid"', body)
        self.assertIn("sizeof(candidate.wifiSsid), true", body)
        setup = self.function_body(self.ino(), "void setup()")
        # setup() never synchronously connects; a portal start replaces any
        # network start while the settings record is invalid.
        self.assertNotIn("mqttClient.connect(", setup)
        self.assertIn("startPortal(", setup)
        self.assertLess(setup.index("startPortal("),
                        setup.index("WiFi.mode(WIFI_STA)"))


class FirmwareStrictJsonTest(FirmwareStaticContractTest):
    """P1.6: MQTT JSON fields are strictly typed — present-but-wrong types and
    non-finite numbers reject the payload; absent fields keep documented
    defaults; a rejected payload leaves the previous state untouched."""

    def ino(self) -> str:
        return self.source("firmware/MicroPad_HA_Controller.ino")

    def test_numeric_and_boolean_fields_are_strictly_typed(self):
        body = self.function_body(self.ino(), "bool parseItem(")
        for field in ("value", "min", "max", "step"):
            self.assertIn(f'numericField(obj["{field}"]', body)
        self.assertIn('booleanField(obj["editable"]', body)
        # The silent-default pattern must be gone.
        self.assertNotIn('obj["value"].is<float>()', body)
        self.assertNotIn('obj["editable"].is<bool>()', body)

    def test_helpers_reject_present_wrong_type_and_non_finite(self):
        numeric = self.function_body(self.ino(), "bool numericField(")
        self.assertIn("value.isNull()", numeric)
        self.assertIn("is<float>()", numeric)
        self.assertIn("std::isfinite(", numeric)
        boolean = self.function_body(self.ino(), "bool booleanField(")
        self.assertIn("is<bool>()", boolean)
        self.assertIn("value.isNull()", boolean)

    def test_rejected_payload_keeps_previous_state_and_no_draw(self):
        current = self.function_body(
            self.ino(), "void onMqttMessage(char *topic, uint8_t *payload, unsigned int length)"
        )
        # The current-page branch commits and draws only on successful parse +
        # authoritative change; every failure path returns untouched.
        self.assertIn("if (parseCurrentPage(root, page)) {", current)
        self.assertIn("requestDraw(", current)
        self.assertIn("contentChanged", current)
        self.assertNotIn("Serial", current)


class FirmwareSleepInterlockTest(FirmwareStaticContractTest):
    """P1.2: the sleep path and the render task serialize through the core
    SleepInterlock inside critical sections; a suspended renderer is never
    mid-draw and hasPending reads are race-free."""

    def ino(self) -> str:
        return self.source("firmware/MicroPad_HA_Controller.ino")

    def test_enter_sleep_grants_interlock_before_suspend(self):
        body = self.function_body(self.ino(), "void enterLightSleep(")
        self.assertIn("sleepInterlock.setSleepEntered(busy, pending)", body)
        self.assertIn("portENTER_CRITICAL(&snapshotMux)", body)
        self.assertIn("if (!granted) return;", body)
        # The suspend must come strictly after the grant.
        self.assertLess(body.index("granted"), body.index("vTaskSuspend("))
        # Wake releases the interlock again.
        self.assertIn("sleepInterlock.wake()", body)
        self.assertIn("pendingAfterWake", body)
        self.assertIn("requestDraw(micropad::DrawReason::StateChange)", body)

    def test_sleep_gate_reads_pending_under_the_mutex(self):
        body = self.function_body(self.ino(), "bool sleepGateReady(")
        self.assertIn("portENTER_CRITICAL(&snapshotMux)", body)
        self.assertIn("snapshotMailbox.hasPending()", body)
        self.assertIn("renderBusy.load", body)
        self.assertIn("portEXIT_CRITICAL(&snapshotMux)", body)

    def test_render_task_claims_through_the_interlock(self):
        body = self.function_body(self.ino(), "void renderTask(")
        self.assertIn("sleepInterlock.tryBeginDraw()", body)
        self.assertIn("if (!mayDraw)", body)
        # On refusal the renderer delays and skips the draw instead of
        # starting a transaction while sleep owns the interlock.
        self.assertIn("vTaskDelay(pdMS_TO_TICKS(20))", body)
        self.assertLess(body.index("if (!mayDraw)"),
                        body.index("drawSnapshot("))
        # renderBusy flips inside the same critical section the sleep path
        # reads, so the busy check cannot race a draw start.
        busy_region = body[body.index("waitForRefreshSpacing();") :
                           body.index("drawSnapshot(")]
        self.assertIn("portENTER_CRITICAL", busy_region)
        self.assertIn("renderBusy.store(true", busy_region)
        # Only the loop task is watchdog-subscribed; the renderer never arms a
        # busy wait that could trip the TWDT.
        self.assertIn("esp_task_wdt_reset()", body)


class FirmwareOptimisationContractTest(FirmwareStaticContractTest):
    """Fixes from the deep-dive optimisation analysis (RAM, robustness,
    responsiveness). Each check pins the behaviour the fix introduced, so a
    later refactor cannot silently undo it."""

    def ino(self) -> str:
        return self.source("firmware/MicroPad_HA_Controller.ino")

    def core_header(self) -> str:
        return self.source("firmware/micropad_core.h")

    # --- RAM: display-only strings clip, identifiers stay strict -----------

    def test_display_only_strings_are_clipped_not_rejected(self):
        # A long HA state or item name must never discard an entire page or
        # catalog payload: the rejection would be invisible on the device.
        text = self.ino()
        helper = self.function_body(text, "bool copyOptionalDisplayText(")
        self.assertIn("safeCopyClip(", helper)
        self.assertIn("jsonString(", helper)
        item = self.function_body(text, "bool parseItem(")
        self.assertIn('copyOptionalDisplayText(obj["state"]', item)
        self.assertIn('copyOptionalDisplayText(obj["unit"]', item)
        self.assertIn("safeCopyClip(out.name, name)", item)
        # Identifiers keep the strict copy: a silently clipped entity id would
        # address the wrong device.
        self.assertIn('copyOptionalString(obj["entity"]', item)
        self.assertIn('copyOptionalString(obj["target_page"]', item)
        page = self.function_body(text, "bool parsePage(")
        self.assertIn("safeCopyClip(out.title, title)", page)
        self.assertIn('copyOptionalString(obj["parent"]', page)
        self.assertIn("bool safeCopyClip(char (&dst)[N], const char *src)",
                      self.core_header())

    def test_display_caps_shrunk_and_identifier_caps_kept(self):
        core = self.core_header()
        self.assertIn("constexpr size_t ITEM_NAME_CAP = 33;", core)
        self.assertIn("constexpr size_t TITLE_CAP = 33;", core)
        self.assertIn("constexpr size_t STATE_CAP = 33;", core)
        # Identifier caps stay deliberately larger than the display caps.
        self.assertIn("constexpr size_t ENTITY_CAP = 97;", core)
        self.assertIn("constexpr size_t PAGE_ID_CAP = 33;", core)

    def test_render_prim_budget_has_measured_headroom(self):
        # The measured worst-case frame is 22 prims; 32 keeps >25 % headroom
        # (testRenderPrimBudgetHeadroom in the host binary proves it).
        self.assertIn("constexpr size_t RENDER_PRIM_CAP = 32;",
                      self.core_header())

    # --- responsiveness: the sleep predicate is sampled -------------------

    def test_sleep_gate_is_sampled_not_per_pass(self):
        self.assertIn("constexpr uint32_t SLEEP_GATE_SAMPLE_MS = 250;",
                      self.core_header())
        body = self.function_body(self.ino(), "bool sleepGateReady(")
        self.assertIn("SLEEP_GATE_SAMPLE_MS", body)
        self.assertIn("lastSleepGateMs", body)
        # The rate limit must run before the expensive held-key matrix pass.
        self.assertLess(body.index("SLEEP_GATE_SAMPLE_MS"),
                        body.index("anyKeyHeld()"))

    # --- robustness: a failed write is a failed connection ----------------

    def test_subscribe_results_are_checked(self):
        body = self.function_body(self.ino(), "void mqttTick(uint32_t nowMs)")
        for name in ("pagesSubscribed", "pageSubscribed", "keymapSubscribed"):
            self.assertIn(name, body)
        self.assertIn("mqttConnectionLost(nowMs)", body)
        self.assertLess(body.index("mqttConnectionLost(nowMs)"),
                        body.index("publishInitialRequests()"))
        self.assertIn("mqttConnectedSinceMs = nowMs;", body)

    def test_connection_lost_helper_drops_the_socket(self):
        body = self.function_body(
            self.ino(), "void mqttConnectionLost(uint32_t nowMs)")
        self.assertIn("mqttClient.disconnect()", body)
        self.assertIn("mqttNetworkClient.stop()", body)
        self.assertIn("mqttNextAttemptMs = nowMs;", body)

    def test_power_publish_failure_is_not_ignored(self):
        publish = self.function_body(
            self.ino(), "bool publishPowerStateRetained(bool usbHost)")
        self.assertIn("return mqttClient.publish", publish)
        tick = self.function_body(self.ino(), "void powerTick(uint32_t nowMs)")
        self.assertEqual(tick.count("mqttConnectionLost(nowMs)"), 2)

    def test_watchdog_subscription_failure_is_handled(self):
        setup = self.function_body(self.ino(), "void setup()")
        self.assertIn("esp_task_wdt_add(NULL) != ESP_OK", setup)
        self.assertLess(setup.index("esp_task_wdt_reconfigure(&wdtCfg)"),
                        setup.rindex("esp_task_wdt_add(NULL)"))
        restore = self.function_body(
            self.ino(),
            "void restoreAfterWake(uint32_t wakeMs, uint64_t sleptUs)")
        self.assertIn("esp_task_wdt_add(NULL) != ESP_OK", restore)
        self.assertIn("esp_task_wdt_reconfigure(&wakeWdtCfg)", restore)

    def test_unreachable_wifi_reopens_the_setup_portal(self):
        self.assertIn("constexpr uint32_t WIFI_FAILS_BEFORE_PORTAL = 24;",
                      self.core_header())
        body = self.function_body(self.ino(), "void wifiTick(uint32_t nowMs)")
        self.assertIn("wifiFailures", body)
        self.assertIn("WIFI_FAILS_BEFORE_PORTAL", body)
        self.assertIn("startPortal(nowMs)", body)
        # A real association clears the failure streak before the next attempt.
        self.assertLess(body.index("wifiFailures = 0;"),
                        body.index("++wifiFailures;"))

    def test_missing_catalog_is_re_requested(self):
        self.assertIn("constexpr uint32_t CATALOG_RETRY_MS = 60000;",
                      self.core_header())
        body = self.function_body(self.ino(),
                                 "void catalogRequestTick(uint32_t nowMs)")
        self.assertIn("catalogReady", body)
        self.assertIn("CATALOG_RETRY_MS", body)
        self.assertIn("GetAllPages", body)

    def test_portal_page_reserves_its_capacity(self):
        body = self.function_body(self.ino(), "String portalPage()")
        self.assertIn("page.reserve(", body)
        self.assertLess(body.index("page.reserve("), body.index("page = F("))
