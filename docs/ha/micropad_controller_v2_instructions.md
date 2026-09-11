# MicroPad Controller v2 — Paste-Anleitung für Bastian

Diese Anleitung ist die **Copy-Paste-Brücke** zwischen der MicroPad-WebUI (`http://192.168.178.103:8080`) und deinem Home Assistant. Sie erweitert den bestehenden `automation.micropad_controller` um die vier neuen Media-Player-Aktionen (`volume_up`, `volume_down`, `media_next`, `media_prev`), die die neue ESP32-S3-Firmware ab Firmware-Stand **Step F-2** des Revamp-Plans aus dem Tasten-Pad bzw. Encoder heraus feuert. Du gehst die fünf Steps der Reihe nach durch. Keine Shell, kein Token-Rumreichen — alles ist HA-eigenes UI-Paste.

## Voraussetzungen

Bevor du loslegst, prüfe, dass diese Bretter wirklich liegen — sonst funktioniert die neue Automation entweder gar nicht oder nur halb:

- **Home Assistant ≥ 2026.9.1** (das ist die Version, die deine Instanz gerade meldet; das Hand-off-Dokument schrieb noch 2025.9.1, das ist veraltet—bitte ignorieren).
- **Mosquitto-Broker** auf `192.168.178.103:1883`, drei User: `micropad`, `hassagent`, `ha`. Die Automation triggert auf Topic `micropad/event`.
- **ESP32-S3 MicroPad** mit der gepatchten Firmware aus Commit-Stand **Step F-2** des Revamp-Plans (`mqtt.setBufferSize(8192)`, USB-Sleep-Fix, MQTT-Overflow-Guard). Diese Firmware veröffentlicht auf `micropad/event` jetzt zusätzlich die Aktionen `volume_up`, `volume_down`, `media_next`, `media_prev` über die gleiche JSON-Struktur wie `toggle`, `press`, `navigate`.
- **Eine erreichbare Media-Player-Entity** in HA. Als Beispiel-Entity dient hier `media_player.homeassistant_media_player_spotify`; ersetze den Wert durch jeden beliebigen Spotify-Connect-Player, Echo, Sonos o. ä. — die Service-Calls unten sind media-player-generisch.
- **HASS.Agent auf deinem Windows-PC** ist installiert und exponiert die dir bekannten Buttons (`pcmitaids_discord_mute/_start/_stop`, `pcmitaids_lock/_restart/_shutdown/_volumemute/_runnotepad`). Für dieses Paste-Paket brauchst du nichts Neues in HASS.Agent außer dem optionalen Schritt in **Step 3**.
- **Der bestehende `automation.micropad_controller`** läuft (mode `queued`, max 10). Wir *erweitern* ihn nur — wir ersetzen ihn nicht.

Was wir **nicht** anfassen:

- Kein ADC-Patch am ESP32 — die neuen Aktionen sind reine MQTT-Events.
- Kein HW-Arbeit am Pad oder an HASS.Agent — nur YAML in HA.
- Kein Firmware-Reflash nötig, wenn du Step F-2 schon drauf hast.

## Step 1 — neue Skripte: scripts.yaml

Öffne in HA **Entwicklerwerkzeuge → Dienste → Aktion aufrufen: `homeassistant.reload_core_services`** ist nicht nötig. Stattdessen: **Einstellungen → Dashboards → rechte obere Ecke → „YAML‑Konfiguration bearbeiten" → Datei `scripts.yaml`** (oder über `ha file editor`, falls installiert).

Füge unten den folgenden Block **an das Ende** deiner bestehenden `scripts.yaml` ein. Die Script-IDs sind bewusst `micropad_media_*` gewählt, damit sie mit deinen `micropad_scene_*` konsistent bleiben und sich nicht mit `mc_pc_*` beißen.

```yaml
# MicroPad v2 — Media-Player scripts (paste-at-end of scripts.yaml)
micropad_media_play_pause:
  alias: "MicroPad Media Play Pause"
  mode: single
  fields:
    entity_id:
      description: "Media player entity to toggle"
      example: "media_player.homeassistant_media_player_spotify"
  sequence:
    - service: media_player.media_play_pause
      target:
        entity_id: "{{ entity_id }}"

micropad_media_next:
  alias: "MicroPad Media Next"
  mode: single
  fields:
    entity_id:
      description: "Media player entity"
      example: "media_player.homeassistant_media_player_spotify"
  sequence:
    - service: media_player.media_next_track
      target:
        entity_id: "{{ entity_id }}"

micropad_media_prev:
  alias: "MicroPad Media Previous"
  mode: single
  fields:
    entity_id:
      description: "Media player entity"
      example: "media_player.homeassistant_media_player_spotify"
  sequence:
    - service: media_player.media_previous_track
      target:
        entity_id: "{{ entity_id }}"

micropad_volume_up:
  alias: "MicroPad Volume Up"
  mode: single
  fields:
    entity_id:
      description: "Media player entity"
      example: "media_player.homeassistant_media_player_spotify"
  sequence:
    - service: media_player.volume_up
      target:
        entity_id: "{{ entity_id }}"

micropad_volume_down:
  alias: "MicroPad Volume Down"
  mode: single
  fields:
    entity_id:
      description: "Media player entity"
      example: "media_player.homeassistant_media_player_spotify"
  sequence:
    - service: media_player.volume_down
      target:
        entity_id: "{{ entity_id }}"

micropad_shuffle_set:
  alias: "MicroPad Shuffle Toggle"
  mode: single
  fields:
    entity_id:
      description: "Media player entity"
      example: "media_player.homeassistant_media_player_spotify"
    shuffle:
      description: "True or False"
      example: true
  sequence:
    - service: media_player.shuffle_set
      target:
        entity_id: "{{ entity_id }}"
      data:
        shuffle: "{{ shuffle }}"

micropad_volume_set:
  alias: "MicroPad Volume Set (0-1)"
  mode: single
  fields:
    entity_id:
      description: "Media player entity"
      example: "media_player.homeassistant_media_player_spotify"
    volume_level:
      description: "0.0 .. 1.0"
      example: 0.5
  sequence:
    - service: media_player.volume_set
      target:
        entity_id: "{{ entity_id }}"
      data:
        volume_level: "{{ volume_level }}"
```

**Speichern → Entwicklerwerkzeuge → YAML → „Skripte neu laden"** (Button „CHECK AND RELOAD" auf der Skripte-Seite reicht auch). Du solltest danach in den Entwicklerwerkzeugen unter „Dienste" sieben neue Einträge sehen: `script.micropad_media_play_pause`, `_next`, `_prev`, `script.micropad_volume_up`, `_down`, `script.micropad_shuffle_set`, `script.micropad_volume_set`.

Warum Skripte und nicht die rohen Service-Calls direkt in der Automation?

1. Sie tauchen in den HASS.Agent-Buttons auf, falls du sie dort je brauchst.
2. Der Spotify-Player-Entity-Beispiel-Wert liegt genau an einer Stelle, nicht in 14 choose-Armen.
3. Du kannst später bequem über Skript-Parameter (`fields`) experimentieren, ohne die Automation zu refactorn.

## Step 2 — `automation.micropad_controller` erweitern

Öffne die Automation über **Einstellungen → Automationen & Szenen → MicroPad Controller → ⋮ → Als YAML bearbeiten** (oder direkt in `automations.yaml`). Wir fügen **vier neue `choose:`-Arme** hinzu — die existierenden Arme und Variablen bleiben unangetastet.

Hier ist die YAML-Diff-Erweiterung, die du in den `actions:`-Block der bestehenden Automation anhängst. Sie nimmt an, dass bereits `variables: action, entity, value, target_page, page_id` am Anfang der Automation stehen und die bestehenden `choose:`-Arme die Aktionen `home`, `navigate`, `toggle`, `on`, `off`, `press`, `edit` etc. abdecken. Wir hängen nur an.

```yaml
# --- new choose arms; append these INSIDE the existing actions: choose: block ---
- conditions:
    - condition: template
      value_template: "{{ action == 'volume_up' }}"
    - condition: template
      value_template: "{{ trigger.entity is defined }}"
  sequence:
    - service: media_player.volume_up
      target:
        entity_id: "{{ trigger.entity }}"
- conditions:
    - condition: template
      value_template: "{{ action == 'volume_down' }}"
    - condition: template
      value_template: "{{ trigger.entity is defined }}"
  sequence:
    - service: media_player.volume_down
      target:
        entity_id: "{{ trigger.entity }}"
- conditions:
    - condition: template
      value_template: "{{ action == 'media_next' }}"
    - condition: template
      value_template: "{{ trigger.entity is defined }}"
  sequence:
    - service: media_player.media_next_track
      target:
        entity_id: "{{ trigger.entity }}"
- conditions:
    - condition: template
      value_template: "{{ action == 'media_prev' }}"
    - condition: template
      value_template: "{{ trigger.entity is defined }}"
  sequence:
    - service: media_player.media_previous_track
      target:
        entity_id: "{{ trigger.entity }}"
```

**Wichtig — wie `trigger.entity` zu dir kommt:** die WebUI serialisiert beim „Generate → Upload to HA"-Klick die pro Taste gewählte Ziel-Entity und bettet sie sowohl in das `keymap`-Payload als auch in den MQTT-Body des `micropad/event`-Triggers ein. Der Trigger-Payload kommt also als JSON der Form

```json
{"action": "volume_up", "entity": "media_player.homeassistant_media_player_spotify", "value": null, "target_page": "spotify", "page_id": 2}
```

in HA an, und HA mappt das auf `trigger.entity` automatisch. Wenn du das prüfen willst: **Entwicklerwerkzeuge → MQTT → abonniere `micropad/event`** und drücke einmal eine der neuen Tasten am Pad.

**Speichern → Automations-Seite neu laden.** In der Liste sollte `automation.micropad_controller` weiterhin `mode: queued` / `max: 10` zeigen und der `last_triggered`-Zähler sollte sich nach dem ersten Tastendruck erhöhen.

Falls du lieber die **Skript-Variante** aus Step 1 verwendest (einheitlicher Stil mit deinen anderen Templates, leichteres Logging in HA), ersetze die obigen vier `sequence:`-Blöcke durch Aufrufe der Skripte:

```yaml
# --- alternative: use script.micropad_volume_up/_down etc. ---
- conditions:
    - condition: template
      value_template: "{{ action == 'volume_up' }}"
    - condition: template
      value_template: "{{ trigger.entity is defined }}"
  sequence:
    - service: script.micropad_volume_up
      data:
        entity_id: "{{ trigger.entity }}"
- conditions:
    - condition: template
      value_template: "{{ action == 'volume_down' }}"
    - condition: template
      value_template: "{{ trigger.entity is defined }}"
  sequence:
    - service: script.micropad_volume_down
      data:
        entity_id: "{{ trigger.entity }}"
- conditions:
    - condition: template
      value_template: "{{ action == 'media_next' }}"
    - condition: template
      value_template: "{{ trigger.entity is defined }}"
  sequence:
    - service: script.micropad_media_next
      data:
        entity_id: "{{ trigger.entity }}"
- conditions:
    - condition: template
      value_template: "{{ action == 'media_prev' }}"
    - condition: template
      value_template: "{{ trigger.entity is defined }}"
  sequence:
    - service: script.micropad_media_prev
      data:
        entity_id: "{{ trigger.entity }}"
```

Wähle **eine** der beiden Varianten — nicht beide gleichzeitig einbauen, sonst feuert jeder Tastendruck zweimal.

## Step 3 — HASS.Agent Settings

**Optional.** Wenn du auf der Discord-Seite deines Pads die Aktion „Deafen" haben willst, lege in **HASS.Agent Settings → Buttons → New Button** einen weiteren Schalter an:

- **Name:** `pcmitaids_discord_deafen`
- **Action:** Discord → Toggle Deafened (`Hotkey: Ctrl+Shift+D` als Fallback, falls deine HASS.Agent-Version das direkte „Deafen"-Event noch nicht kennt)

Danach taucht in HA eine `button.pcmitaids_discord_deafen`-Entity auf, die du in der bestehenden `script.mc_pc_discord_*`-Familie als Wrapper hinzufügen kannst — exakt nach dem Muster der existierenden `script.mc_pc_discord_mute`:

```yaml
# Optional: add to scripts.yaml only if you wired the HASS.Agent deafen button above
mc_pc_discord_deafen:
  alias: "MicroPad PC Discord Deafen"
  mode: single
  sequence:
    - service: button.press
      target:
        entity_id: button.pcmitaids_discord_deafen
```

Wenn du Discord-Deafen (noch) nicht brauchst, skippe diesen Step komplett. Der Rest dieser Anleitung funktioniert ohne ihn.

## Step 4 — Test sequence

Führe die folgenden Checks der Reihe nach durch, **eine Zahl nach der anderen**, und warte jeweils auf das erwartete Feedback:

1. **MQTT-Eingang prüfen.** In HA: **Entwicklerwerkzeuge → MQTT → „Listen to a topic" → Topic `micropad/event` → Start Listening**. Alternativ von oserver aus:
   `mosquitto_sub -h 192.168.178.103 -u micropad -P "$(awk -F= '/broker=/ {gsub(/^.+broker=/,""); print}' ~/.config/micropad_mqtt.pass)" -t 'micropad/event' -v`.
   Drücke am Pad die Taste, die in der WebUI auf eine `media_player.*` gebunden ist. Erwartet: ein JSON-Payload der Form
   `{"action":"volume_up","entity":"media_player.homeassistant_media_player_spotify","value":null,"target_page":"…","page_id":N}`.
   *Wenn kein Payload → `automation.micropad_controller` triggert nicht → zur Roll-back-Sektion.*
2. **Automation-Flow.** In HA: **Entwicklerwerkzeuge → Automatisierung → MicroPad Controller → Traces** (oder die bestehende UI-Trace auf der Automation-Seite). Erwartet: ein roter/grüner Trace-Block pro Tastendruck, der den passenden choose-Arm aktiviert (z. B. `volume_up`) und genau einen Service-Call absetzt.
3. **Service-Call-Erfolg.** In HA: **Entwicklerwerkzeuge → Dienste → `media_player.volume_up` → Aufrufen mit Zielerentity `media_player.homeassistant_media_player_spotify`**. Erwartet: keine Fehlermeldung, der Lautstärkewert deines Spotify-Players geht einen Schritt nach oben.
4. **End-to-End.** Drücke noch einmal die Pad-Taste, die `volume_up` gebunden hat. Erwartet: dein Spotify-Connect-Ziel (Echo, Sonos, Desktop-App etc.) wird hörbar lauter. Dasselbe nochmal mit `volume_down`, `media_next`, `media_prev`.
5. **Negative Test.** Drücke eine Taste, die auf einen Lichtschalter (`light.wohnzimmer` o. ä.) gebunden ist. Erwartet: der Schalter reagiert wie immer — d. h. wir haben die bestehenden choose-Arme nicht beschädigt.
6. **Queueing-Verhalten.** Drücke innerhalb von 2 s 12× dieselbe `volume_up`-Taste. Erwartet: der Lautstärkewert steigt in 10 hörbaren Stufen, die letzten zwei landen in der Automation-Queue (`mode: queued, max: 10`) ohne dass HA eine „too many triggers"-Warnung wirft.

Wenn alle sechs Punkte grün sind, ist die v2-Erweiterung sauber durch.

## Step 5 — Keymap re-publish

Damit die neue Spotify-Seite (oder die Seite, auf der du die neuen Aktionen platziert hast) dem Pad auch wirklich die richtigen Tasten-IDs mit den richtigen Entities mitteilt, musst du das Keymap-Payload **einmal frisch erzeugen** und an HA liefern. Die Firmware überschreibt ihr RAM-Keymap (`KeyBinding keymap[14]`) bei jedem Boot aus dem aktuell retained `micropad/keymap`-Topic — wenn das Topic nicht aktualisiert wurde, läuft das Pad auf dem alten Mapping.

**Vorgehen:**

1. In der WebUI `http://192.168.178.103:8080` die Seite, die du ausgebaut hast (Spotify / Media), öffnen.
2. **Generate → Upload to HA** klicken (das ist der Button „Push to Home Assistant" oben rechts im Editor). Die WebUI ruft das HA-Bridge-Endpoint und schreibt sowohl `automation.micropad_controller` als auch das `micropad/keymap` retained Topic neu.
3. **MQTT-Check**, dass das neue Payload angekommen ist: in HA-Entwicklerwerkzeugen **MQTT → Listen to `micropad/keymap`** → du solltest eine JSON sehen, deren `keymap`-Objekt für deine neuen Slots die `action: "volume_up"` / `"volume_down"` / `"media_next"` / `"media_prev"` enthält, mit der korrekten `entity` pro Taste.
4. **Pad aufwecken.** Wenn das Pad gerade schläft, irgendeine Taste kurz drücken — das weckt es aus dem Light-Sleep, und im `loop()` wird `applyKeymap()` aufgerufen, sobald das retained Topic ankommt. Die Status-Bar oben rechts („MQTT / WiFi / Trying") flackert kurz, wenn das neue Keymap eingelesen wurde.
5. **Sichtkontrolle auf dem Display.** Die neue Seite sollte jetzt die korrekten Aktions-Labels für die Encoder- und Tasten-Bindings zeigen (z. B. „Vol +", „Next", „Prev"), wie du sie in der WebUI benannt hast.

Wenn der Schritt nicht klappt (WebUI sagt „Upload fehlgeschlagen"), liegt das typischerweise an einem abgelaufenen HA-Long-Lived-Token in `settings.ha_token` in `~/micropad/data/config.json` — dann muss das über die WebUI-Settings neu erstellt werden, das ist aber out-of-scope dieser Anleitung.

## Roll-back (just in case)

Falls etwas schiefgeht und du die v2-Erweiterung wieder raus haben willst, ohne den ganzen MicroPad-Stack zu zerlegen:

1. **Automation-Snapshot machen.** Wenn du schon git-trackte `automations.yaml` und `scripts.yaml` hast, ist das ein einfaches `git checkout automations.yaml scripts.yaml`. Wenn nicht, kopier die beiden Dateien vorher nach `/tmp/ha-backup-$(date +%F)/` — dann bist du in einer Minute zurück auf den Zustand vor diesem Paste-Paket.
2. **Automation-Arme entfernen.** Öffne `automation.micropad_controller` in HA, geh in den YAML-Editor und lösche **nur die vier neuen `choose:`-Arme** (`volume_up`, `volume_down`, `media_next`, `media_prev`). Alles andere bleibt stehen. Speichern → Automations-Seite neu laden.
3. **Scripts entfernen.** Lösche in `scripts.yaml` die sieben neuen Einträge (`micropad_media_play_pause`, `_next`, `_prev`, `micropad_volume_up`, `_down`, `micropad_shuffle_set`, `micropad_volume_set` — und falls du Step 3 aktiviert hast, auch `mc_pc_discord_deafen`). Speichern → Skripte neu laden.
4. **WebUI-Keymap regenerieren.** Erzeuge in `http://192.168.178.103:8080` das Keymap **ohne** die neuen Media-Slots neu und klicke „Generate → Upload to HA". Das retained `micropad/keymap` enthält dann wieder nur die alten Aktionen, und das Pad überschreibt sein RAM-Keymap beim nächsten Wake darauf.
5. **Firmware bleibt unangetastet.** Es ist kein Reflash nötig — die vier neuen Aktions-Strings werden vom Pad einfach ignoriert, wenn die Automation sie nicht mehr abholt. Solange du den MQTT-Buffer-Overflow-Guard in der Firmware nicht entfernst, passiert nichts Schlimmes.

Wenn danach immer noch etwas nicht stimmt: Pad einmal ausschalten, **5 s** warten (WDT-Reset), wieder einschalten. Das resettet die `keymap[14]`-Defaults aus `loadDefaultKeymap()` und gibt dir einen sauberen Stand zum Weiterexperimentieren.
