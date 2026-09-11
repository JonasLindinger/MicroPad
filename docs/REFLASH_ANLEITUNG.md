# MicroPad Firmware v4 — Reflash-Anleitung (USB-Sleep-Fix)

Datei: `~/micropad/firmware/MicroPad_HA_Controller_v4/MicroPad_HA_Controller_v4.ino`
(Ordner MUSS genau so heißen wie die .ino, sonst startet der Sketch nicht)

## Was wurde geändert (gegenüber GitHub v4)
1. **Kein Light-Sleep mehr, wenn USB am PC hängt**
   - Erkennung: `tud_cdc_n_connected(0) || (bool)USBSerial` (TinyUSB) — true,
     wenn ein echter USB-Host (PC) die USB-CDC-Verbindung aufgebaut hat.
   - Reines Ladegerät ohne Datenleitung → meldet false → Pad schläft normal.
   - Logik: `loop()` → `if (!active && !usbHostAttached())` → nur dann Sleep.
2. **Broker-Zugangsdaten:** mqttServer = 192.168.178.103, mqttUser = micropad,
   mqttPass = **Platzhalter** (`replace-me`, Zeile 226). Vor dem Flashen den
   echten Wert eintragen (aus `~/.config/micropad_mqtt.pass` auf oserver oder
   aus der lokalen Arbeitskopie `~/micropad/firmware/MicroPad_HA_Controller_v4/`).
   → Die GitHub-Kopie enthält bewusst KEIN echtes Passwort.
3. **MQTT-Buffer 4096 → 8192 + Overflow-Guard**
   - Der Seiten-Katalog (`micropad/pages/all`) ist ~6 KB groß; PubSubClient
     schneidet größere Nachrichten still ab (statt sie zu verwerfen) → der
     Seiten-Cache im Pad wurde nie aktualisiert und Navigation zeigte
     veraltete Reihenfolge (z. B. "Lichter" statt "Steckdosen" als ersten
     Eintrag unter HomeAssistant). Mit 8192 passt der Katalog komplett.
   - Guard in `mqttCallback()`: `payload[length]='\0'` konnte bei saturiertem
     Buffer 1 Byte hinter den Heap-Buffer schreiben (Memory-Corruption).

## Flashen (Arduino IDE)
1. Arduino IDE öffnen, Ordner `MicroPad_HA_Controller_v4` öffnen.
2. Board: **ESP32S3 Dev Module** (esp32 core 3.x)
3. Board-Settings (Tools):
   - USB CDC On Boot: **Enabled**
   - PSRAM: **OPI PSRAM**
   - Flash Size: **8MB (64Mb)**
   - Partition Scheme: **8M with spiffs**
   - Flash Mode: QIO 80MHz; Upload Speed 921600 (bei Problemen 115200)
4. Libraries (falls nicht installiert): GxEPD2, PubSubClient, **ArduinoJson 7.x**
5. Port: native USB-Serial/JTAG (`/dev/ttyACM0` / COM)
   - Kein Port / "No serial data": BOOT-Taste halten → RESET tippen → BOOT loslassen → Upload
6. Upload.

## Nach dem Flashen
- WiFi/MQTT bleiben in NVS gespeichert (überlebt Reflash) — Pad verbindet sich
  wieder zu oserver-Broker. Falls Netz weg: SETTINGS-Taste (R2C2) → Portal
  `MicroPad-Setup` / `micropad123` → http://192.168.4.1
- Menü kommt weiterhin von HA — kein weiterer Reflash nötig.

## Test
- Pad am PC hängen → 60 s warten → Display bleibt an, MQTT bleibt verbunden.
- Pad vom Kabel → 60 s warten → "going to sleep" im Serial Monitor, Wake auf Tastendruck.