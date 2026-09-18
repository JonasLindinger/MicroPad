# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Tests for the streamed e-paper frame conversion and raw-file source."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "micropad_streamer"))

from epd_frame import (  # noqa: E402
    FRAME_BYTES,
    PANEL_H,
    PANEL_W,
    gray_to_monob,
    monob_to_gray,
)

from streamer import RawFileSource  # noqa: E402


def test_byte_count_matches_contract_limit():
    # Contract: frame_bytes = 4736 (296 x 128 / 8).
    gray = np.zeros((PANEL_H, PANEL_W), dtype=np.uint8)
    blob = gray_to_monob(gray)
    assert len(blob) == FRAME_BYTES == 4736


def test_all_black_frame_is_all_zero_bytes():
    gray = np.zeros((PANEL_H, PANEL_W), dtype=np.uint8)
    assert gray_to_monob(gray) == b"\x00" * FRAME_BYTES


def test_all_white_frame_is_all_ones():
    gray = np.full((PANEL_H, PANEL_W), 255, dtype=np.uint8)
    assert gray_to_monob(gray) == b"\xff" * FRAME_BYTES


def test_threshold_splits_at_boundary():
    gray = np.full((PANEL_H, PANEL_W), 127, dtype=np.uint8)
    assert gray_to_monob(gray, threshold=128) == b"\x00" * FRAME_BYTES
    gray = np.full((PANEL_H, PANEL_W), 128, dtype=np.uint8)
    assert gray_to_monob(gray, threshold=128) == b"\xff" * FRAME_BYTES


def test_msb_first_row_major_layout():
    # A single white pixel at panel row 0, column 0 must set only the MSB of
    # byte 0; a white pixel at row 0, column 7 must set only its LSB.
    gray = np.zeros((PANEL_H, PANEL_W), dtype=np.uint8)
    gray[0, 0] = 255
    blob = gray_to_monob(gray)
    assert blob[0] == 0b10000000
    assert blob[1:].count(b"\x00") == FRAME_BYTES - 1

    gray = np.zeros((PANEL_H, PANEL_W), dtype=np.uint8)
    gray[0, 7] = 255
    blob = gray_to_monob(gray)
    assert blob[0] == 0b00000001

    # Row 1 starts at byte 37 (296 bits / 8); a pixel at row 1, column 0
    # touches only that byte.
    gray = np.zeros((PANEL_H, PANEL_W), dtype=np.uint8)
    gray[1, 0] = 255
    blob = gray_to_monob(gray)
    assert blob[37] == 0b10000000
    assert blob[0] == 0


def test_roundtrip_preserves_thresholded_image():
    rng = np.random.default_rng(7)
    gray = rng.integers(0, 256, size=(PANEL_H, PANEL_W), dtype=np.uint8)
    back = monob_to_gray(gray_to_monob(gray, threshold=128))
    expected = (gray >= 128).astype(np.uint8) * 255
    assert np.array_equal(back, expected)


def test_wrong_shape_rejected():
    with pytest.raises(ValueError):
        gray_to_monob(np.zeros((100, 296), dtype=np.uint8))
    with pytest.raises(ValueError):
        gray_to_monob(np.zeros((128, 300), dtype=np.uint8))
    with pytest.raises(ValueError):
        gray_to_monob(np.zeros((128, 296, 3), dtype=np.uint8))


def test_raw_file_source_plays_every_frame(tmp_path):
    frames = [bytes([i % 256]) * FRAME_BYTES for i in range(5)]
    path = tmp_path / "frames.raw"
    path.write_bytes(b"".join(frames))
    src = RawFileSource(str(path))
    played = []
    while True:
        frame = src.read_frame()
        if frame is None:
            break
        played.append(frame)
    assert played == frames


def test_raw_file_source_rejects_partial_frame(tmp_path):
    path = tmp_path / "bad.raw"
    path.write_bytes(b"\x00" * (FRAME_BYTES + 10))
    with pytest.raises(AssertionError):
        RawFileSource(str(path))