"""
TTS — Text-to-Speech via Kokoro ONNX.
Provides blocking and async wrappers for generating and playing speech.
"""

import asyncio
import logging
import os
import threading
from typing import Optional

log = logging.getLogger("tts")

# ── Configuration ────────────────────────────────────────────────────────
_MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
MODEL_PATH  = os.path.join(_MODEL_DIR, "kokoro-v1.0.onnx")
VOICES_PATH = os.path.join(_MODEL_DIR, "voices-v1.0.bin")
VOICE  = "af_sarah"   # Default voice (American female)
SPEED  = 1.0
LANG   = "en-us"

# ── Lazy-loaded singleton ────────────────────────────────────────────────
_kokoro = None
_lock = threading.Lock()


def _get_kokoro():
    """Lazy-load the Kokoro model (once)."""
    global _kokoro
    if _kokoro is not None:
        return _kokoro
    with _lock:
        if _kokoro is not None:
            return _kokoro
        try:
            from kokoro_onnx import Kokoro
            log.info("Loading Kokoro TTS model from %s …", MODEL_PATH)
            _kokoro = Kokoro(MODEL_PATH, VOICES_PATH)
            log.info("Kokoro TTS ready  ✓  (voice=%s, speed=%.1f)", VOICE, SPEED)
        except FileNotFoundError:
            log.error(
                "Kokoro model files not found at %s. "
                "Download them from: "
                "https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0",
                _MODEL_DIR,
            )
            raise
    return _kokoro


def speak(text: str, voice: str = VOICE, speed: float = SPEED) -> None:
    """
    Generate speech from text and play it through speakers (blocking).
    """
    if not text or not text.strip():
        return
    try:
        import sounddevice as sd
        kokoro = _get_kokoro()
        # Truncate very long text to avoid long TTS delays
        if len(text) > 500:
            text = text[:497] + "..."
        samples, sample_rate = kokoro.create(text, voice=voice, speed=speed, lang=LANG)
        sd.play(samples, sample_rate)
        sd.wait()
    except Exception as e:
        log.warning("TTS playback failed: %s", e)


def speak_async(text: str, voice: str = VOICE, speed: float = SPEED) -> None:
    """
    Generate and play speech in a background thread (non-blocking).
    Safe to call from async or sync contexts.
    """
    thread = threading.Thread(target=speak, args=(text, voice, speed), daemon=True)
    thread.start()
