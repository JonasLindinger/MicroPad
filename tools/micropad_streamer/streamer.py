# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Stream videos to the MicroPad e-paper player mode over MQTT.

The pad's `player` action opens the streamed-video mode and steers playback
through `micropad/player/control` ("open", "close", "toggle", "restart",
"next", "prev"). This service:

* reads the video queue retained on `micropad/player/queue` (published by the
  config generator: {"videos": [{"name", "url"}, ...]}),
* plays any entry live with yt-dlp -> ffmpeg, never touching disk: yt-dlp
  writes the media to a pipe, ffmpeg scales it to the panel's 296x128 and
  emits raw grayscale frames, and thresholding happens in-process,
* sends one 4736-byte frame per `micropad/display/frame` message and waits for
  the pad's `micropad/player/ready` ack before sending the next one, so
  playback runs at the panel's real refresh rate,
* publishes playback status retained on `micropad/player/state`.

A local raw frame file (`--file`, already in the 4736-byte wire format) can be
played instead of the MQTT queue for hardware tests.

Usage:
    python3 tools/micropad_streamer/streamer.py --broker 192.168.0.17 \
        --user micropad --password ... [--threshold 128] [--file frames.raw]
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Optional, Protocol

import paho.mqtt.client as mqtt

from epd_frame import FRAME_BYTES, gray_to_monob

TOPIC_FRAME = "micropad/display/frame"
TOPIC_READY = "micropad/player/ready"
TOPIC_STATE = "micropad/player/state"
TOPIC_QUEUE = "micropad/player/queue"
TOPIC_CONTROL = "micropad/player/control"

ACK_TIMEOUT_S = 12.0
PAUSE_RETRY_S = 8.0


class Source(Protocol):
    def read_frame(self) -> Optional[bytes]: ...
    def suspend(self) -> None: ...
    def resume(self) -> None: ...
    def close(self) -> None: ...


class RawFileSource:
    """Plays a pre-encoded 4736-byte-frame file (for hardware tests)."""

    def __init__(self, path: str):
        self._data = open(path, "rb").read()
        assert len(self._data) % FRAME_BYTES == 0, "file is not a multiple of 4736"
        self._pos = 0

    def read_frame(self) -> Optional[bytes]:
        if self._pos >= len(self._data):
            return None
        frame = self._data[self._pos:self._pos + FRAME_BYTES]
        self._pos += FRAME_BYTES
        return frame

    def suspend(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def close(self) -> None:
        pass


class FfmpegSource:
    """Live yt-dlp -> ffmpeg pipe. Nothing is written to disk."""

    def __init__(self, url: str, threshold: int, log):
        self._url = url
        self._threshold = threshold
        self._log = log
        self._yt: Optional[subprocess.Popen] = None
        self._ff: Optional[subprocess.Popen] = None

    @staticmethod
    def _ffmpeg_filter(threshold: int) -> str:
        # scale with area averaging, drop to gray; the threshold happens in
        # Python so it is deterministic and testable.
        return f"scale=296:128:flags=area,format=gray"

    def start(self) -> None:
        best = "bv[height<=480]/b[height<=480]/bv/b"
        self._yt = subprocess.Popen(
            ["yt-dlp", "-f", best, "--no-playlist", "-o", "-", self._url],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if not self._yt.stdout:
            raise RuntimeError("yt-dlp produced no output pipe")
        self._ff = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-i", "pipe:0",
             "-vf", self._ffmpeg_filter(self._threshold),
             "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
            stdin=self._yt.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def read_frame(self) -> Optional[bytes]:
        if not self._ff or not self._ff.stdout:
            return None
        raw = self._ff.stdout.read(296 * 128)
        if len(raw) != 296 * 128:
            return None  # EOF or decode error
        import numpy as np

        gray = np.frombuffer(raw, dtype=np.uint8).reshape(128, 296)
        return gray_to_monob(gray, self._threshold)

    def suspend(self) -> None:
        if self._ff and self._ff.poll() is None:
            os.kill(self._ff.pid, signal.SIGSTOP)

    def resume(self) -> None:
        if self._ff and self._ff.poll() is None:
            os.kill(self._ff.pid, signal.SIGCONT)

    def close(self) -> None:
        for proc in (self._ff, self._yt):
            if proc and proc.poll() is None:
                proc.kill()
        for proc in (self._ff, self._yt):
            if proc:
                proc.wait()


class Player:
    """Ack-paced MQTT player: publishes frames only after the pad acks."""

    def __init__(self, broker: str, user: str, password: str,
                 threshold: int = 128, file_path: Optional[str] = None,
                 client_id: str = "micropad-player"):
        self._broker = broker
        self._user = user
        self._password = password
        self._threshold = threshold
        self._file_path = file_path
        self._client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)
        self._client.username_pw_set(user, password)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

        self._lock = threading.Lock()
        self._ack_evt = threading.Event()          # set when ready arrives
        self._queue: list[dict] = []
        self._index = 0
        self._playing = False                      # user asked for playback
        self._paused = False
        self._source: Optional[Source] = None      # RawFileSource/FfmpegSource
        self._source_lock = threading.Lock()
        self._last_ack_at = time.monotonic() - ACK_TIMEOUT_S
        self._fps_log: list[float] = []

    # -- MQTT ----------------------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc, props=None):
        if rc != 0:
            return
        for topic in (TOPIC_QUEUE, TOPIC_CONTROL, TOPIC_READY, TOPIC_STATE):
            client.subscribe(topic)

    def _on_message(self, client, userdata, msg):
        try:
            if msg.topic == TOPIC_READY:
                self._ack_evt.set()
                return
            if msg.topic == TOPIC_QUEUE:
                self._load_queue(msg.payload)
                return
            if msg.topic == TOPIC_CONTROL:
                self._on_command(msg.payload.decode(errors="replace").strip())
        except Exception as exc:
            print(f"handler error: {exc}", flush=True)

    def _load_queue(self, payload):
        data = json.loads(payload)
        videos = data.get("videos") or []
        self._queue = [
            {"name": str(v.get("name", "")), "url": str(v.get("url", ""))}
            for v in videos if v.get("name") and v.get("url")
        ]
        if not self._file_path:
            print(f"queue: {len(self._queue)} videos", flush=True)

    def _on_command(self, cmd: str):
        print(f"control: {cmd}", flush=True)
        if cmd == "open":
            # Opening the player mode must NOT start playback: the pad shows
            # its title/status screen and the operator presses play (Enter ->
            # "toggle") to begin. Restarting an already-running video is left
            # untouched.
            if not (self._playing and not self._paused):
                self._publish_state()
        elif cmd == "close":
            self._stop_playback()
        elif cmd == "toggle":
            self._toggle()
        elif cmd == "restart":
            self._restart()
        elif cmd == "next":
            self._step(+1)
        elif cmd == "prev":
            self._step(-1)

    # -- control -------------------------------------------------------------
    def _publish_state(self):
        video = self._queue[self._index] if self._queue else None
        avg = (sum(self._fps_log) / len(self._fps_log)) if self._fps_log else 0
        state = {
            "title": (video or {}).get("name", ""),
            "status": ("paused" if self._paused else "playing") if self._playing
                      else "stopped",
            "index": self._index if self._queue else 0,
            "videos": len(self._queue),
        }
        if avg:
            state["fps"] = round(avg, 2)
        self._client.publish(TOPIC_STATE, json.dumps(state), qos=0, retain=True)

    def _start_source(self) -> bool:
        if self._file_path:
            self._source = RawFileSource(self._file_path)
            return True
        if not self._queue:
            self._publish_state()
            print("no videos in queue", flush=True)
            return False
        video = self._queue[self._index]
        print(f"playing: {video['name']} ({video['url']})", flush=True)
        src = FfmpegSource(video["url"], self._threshold, self._client)
        src.start()
        self._source = src
        return True

    def _start_playback(self):
        with self._lock:
            if self._playing and not self._paused:
                return
            if self._playing and self._paused:
                self._paused = False
                with self._source_lock:
                    self._source.resume()
                self._publish_state()
                return
            self._playing = True
            self._paused = False
            ok = self._start_source()
            if not ok:
                self._playing = False
                return
            self._publish_state()

    def _stop_playback(self):
        with self._lock:
            self._playing = False
            self._paused = False
            with self._source_lock:
                if self._source:
                    self._source.close()
                    self._source = None
            self._publish_state()

    def _toggle(self):
        with self._lock:
            if not self._playing:
                self._playing = True
                self._paused = False
                ok = self._start_source()
                if not ok:
                    self._playing = False
                    return
            else:
                self._paused = not self._paused
                with self._source_lock:
                    (self._source.resume() if not self._paused
                     else self._source.suspend())
            self._publish_state()

    def _restart(self):
        with self._lock:
            if not self._playing:
                return
            with self._source_lock:
                if self._source:
                    self._source.close()
                ok = self._start_source()
                if not ok:
                    self._playing = False
            self._publish_state()

    def _step(self, delta: int):
        with self._lock:
            if not self._queue:
                return
            was_playing = self._playing
            with self._source_lock:
                if self._source:
                    self._source.close()
                    self._source = None
            self._index = (self._index + delta) % len(self._queue)
            if was_playing:
                ok = self._start_source()
                if not ok:
                    self._playing = False
            self._publish_state()

    # -- frame loop ----------------------------------------------------------
    def _frame_loop(self):
        while True:
            with self._lock:
                if not (self._playing and not self._paused):
                    time.sleep(0.2)
                    continue
                try:
                    with self._source_lock:
                        frame = self._source.read_frame() if self._source else None
                except Exception:
                    frame = None
            if frame is None:
                # EOF: advance to the next video, or stop at the end.
                if self._file_path:
                    self._stop_playback()
                    print("file done", flush=True)
                    continue
                with self._lock:
                    if self._playing and len(self._queue) > 1:
                        self._index = (self._index + 1) % len(self._queue)
                        with self._source_lock:
                            self._source.close()
                            self._source = None
                            ok = self._start_source()
                        if not ok:
                            self._playing = False
                        self._publish_state()
                    else:
                        self._stop_playback()
                        print("playback finished", flush=True)
                time.sleep(0.2)
                continue
            self._client.publish(TOPIC_FRAME, frame, qos=0)
            t0 = time.monotonic()
            if not self._ack_evt.wait(ACK_TIMEOUT_S):
                # Pad did not ack (reconnect, mode closed): keep the frame
                # published a couple of times, then give up on pacing.
                print("no ack - re-publishing frame", flush=True)
                self._client.publish(TOPIC_FRAME, frame, qos=0)
                self._ack_evt.wait(PAUSE_RETRY_S)
            else:
                # only count the ack latency when the ack is timely
                dt = time.monotonic() - t0
                self._fps_log.append(1.0 / max(dt, 1e-3))
                if len(self._fps_log) > 30:
                    self._fps_log.pop(0)
            self._ack_evt.clear()
            self._last_ack_at = time.monotonic()

    # -- main ----------------------------------------------------------------
    def run(self):
        import numpy  # noqa: F401  (import cost check + ensure dependency)

        self._client.connect(self._broker, 1883, keepalive=30)
        self._client.loop_start()
        thread = threading.Thread(target=self._frame_loop, daemon=True)
        thread.start()
        print("streamer ready", flush=True)
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            with self._source_lock:
                if self._source:
                    self._source.close()
            self._client.loop_stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broker", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--threshold", type=int, default=128,
                        help="gray >= threshold becomes white (default 128)")
    parser.add_argument("--file", default=None,
                        help="play a local raw 4736-byte-frame file instead "
                             "of the MQTT queue")
    args = parser.parse_args()
    Player(args.broker, args.user, args.password,
           threshold=args.threshold, file_path=args.file).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())