# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Convert an 8-bit gray frame into the MicroPad streamed-frame format.

The MicroPad player mode renders raw 1-bit frames on the 296x128 e-paper
panel. The wire format (contract: `frame_bytes` = 4736):

* exactly 296 x 128 pixels, row-major, 1 byte per 8 pixels
* bits are MSB-first: the first byte carries panel row 0, columns 0..7, with
  column 0 in the most significant bit
* a set bit (1) is WHITE — the same convention the Bad Apple player used, so
  any source chain that already emits monob frames can stay unchanged

Grayscale input is thresholded: a pixel >= threshold becomes white (1),
everything below becomes black (0).
"""

from __future__ import annotations

import numpy as np

PANEL_W = 296
PANEL_H = 128
FRAME_BYTES = PANEL_W * PANEL_H // 8
DEFAULT_THRESHOLD = 128


def gray_to_monob(gray: np.ndarray, threshold: int = DEFAULT_THRESHOLD) -> bytes:
    """Threshold a (PANEL_H, PANEL_W) uint8 grayscale array into the 4736-byte
    MSB-first bit=white wire frame.

    Raises ValueError for a wrong shape; any array dtype with an ordering is
    accepted (the comparison decides), but 0..255 uint8 is the intended input.
    """
    if gray.shape != (PANEL_H, PANEL_W):
        raise ValueError(
            f"expected ({PANEL_H}, {PANEL_W}) gray frame, got {gray.shape}"
        )
    flat = (gray >= threshold).reshape(-1)  # True = white = bit 1
    n = flat.size
    padded = np.zeros(((n + 7) // 8) * 8, dtype=bool)
    padded[:n] = flat
    return np.packbits(padded).tobytes()


def monob_to_gray(blob: bytes) -> np.ndarray:
    """Inverse of gray_to_monob for tests/display: bit 1 -> 255, bit 0 -> 0."""
    if len(blob) != FRAME_BYTES:
        raise ValueError(f"expected {FRAME_BYTES} bytes, got {len(blob)}")
    bits = np.unpackbits(np.frombuffer(blob, dtype=np.uint8)).astype(np.uint8)
    return (bits * 255).reshape(PANEL_H, PANEL_W)