"""Shared Gemini selection for audio, text and screenshot requests."""
import os

from .model import require

DEFAULT_MODEL = "gemini-3.8-flash"


def gemini_model() -> str:
    model = os.environ.get("PHONE_AI_GEMINI_MODEL", "").strip() or DEFAULT_MODEL
    require("flash-lite" not in model.lower(), "flash_lite_disabled")
    # Keep every non-lite caller on the configured 3.8 Flash contract. Older
    # user-level environment values (for example gemini-2.5-flash) must not
    # silently route requests to a different model.
    return DEFAULT_MODEL
