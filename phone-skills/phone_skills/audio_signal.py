"""Measure signed 16-bit mono PCM before audio routing."""
from __future__ import annotations

import os
import struct
from typing import Any


def pcm_signal(pcm: bytes) -> dict[str, Any]:
    """Cheap fail-closed voice gate for signed 16-bit mono PCM."""
    if len(pcm) < 640 or len(pcm) % 2:
        return {"speech_like": False, "rms": 0, "voiced_frames": 0, "frames": 0}
    samples = struct.unpack("<%dh" % (len(pcm) // 2), pcm)
    frame = 320  # 20 ms at 16 kHz
    threshold = int(os.environ.get("PHONE_AI_MIN_RMS", "220"))
    values = []
    for start in range(0, len(samples) - frame + 1, frame):
        part = samples[start:start + frame]
        rms = int((sum(x * x for x in part) / frame) ** 0.5)
        values.append(rms)
    voiced = sum(r >= threshold for r in values)
    overall = int((sum(x * x for x in samples) / len(samples)) ** 0.5)
    return {"speech_like": voiced >= 2 and overall >= threshold,
            "rms": overall, "voiced_frames": voiced, "frames": len(values)}
