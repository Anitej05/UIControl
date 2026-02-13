"""
Smart Turn v3 — Standalone Inference Module
────────────────────────────────────────────
Silero VAD   : Neural network speech/silence detection (ONNX)
Smart Turn v3: ML-based end-of-turn detection (ONNX, Whisper Tiny backbone)

Auto-downloads both models on first run to ./models/
Audio format : 16 kHz mono float32 PCM

Based on: https://github.com/pipecat-ai/smart-turn
"""

from __future__ import annotations

import logging
import os
import time
import urllib.request
from pathlib import Path

import numpy as np
import onnxruntime as ort

log = logging.getLogger("smart-turn")

# ─── Model paths & URLs ─────────────────────────────────────────────────────
MODELS_DIR = Path(__file__).parent / "models"

SILERO_VAD_URL = (
    "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"
)
SILERO_VAD_PATH = MODELS_DIR / "silero_vad.onnx"

SMART_TURN_URL = (
    "https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/main/smart-turn-v3.2-cpu.onnx"
)
SMART_TURN_PATH = MODELS_DIR / "smart-turn-v3.2-cpu.onnx"

# ─── Audio constants ────────────────────────────────────────────────────────
SAMPLE_RATE = 16_000
SILERO_CHUNK = 512          # Silero VAD native chunk size at 16 kHz
MAX_TURN_SECS = 8           # Smart Turn maximum input length


# ═════════════════════════════════════════════════════════════════════════════
#  SILERO VAD — Neural network speech probability
# ═════════════════════════════════════════════════════════════════════════════
class SileroVAD:
    """
    Minimal Silero VAD ONNX wrapper for 16 kHz mono, chunk_size=512.
    Returns per-chunk speech probability [0.0, 1.0].
    """

    CONTEXT_SIZE = 64  # Silero internal context at 16 kHz
    RESET_INTERVAL = 5.0  # seconds — reset internal state periodically

    def __init__(self, model_path: str | Path | None = None):
        path = str(model_path or _ensure_model(SILERO_VAD_PATH, SILERO_VAD_URL))
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self.session = ort.InferenceSession(
            path, providers=["CPUExecutionProvider"], sess_options=opts,
        )
        self._state: np.ndarray | None = None
        self._context: np.ndarray | None = None
        self._last_reset: float = 0.0
        self._init_states()
        log.info("Silero VAD loaded  ✓")

    def _init_states(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, self.CONTEXT_SIZE), dtype=np.float32)
        self._last_reset = time.monotonic()

    def _maybe_reset(self) -> None:
        if (time.monotonic() - self._last_reset) >= self.RESET_INTERVAL:
            self._init_states()

    def prob(self, chunk_f32: np.ndarray) -> float:
        """
        Compute speech probability for one 512-sample chunk (float32, mono).
        Returns scalar float in [0.0, 1.0].
        """
        x = np.reshape(chunk_f32, (1, -1))
        if x.shape[1] != SILERO_CHUNK:
            raise ValueError(f"Expected {SILERO_CHUNK} samples, got {x.shape[1]}")

        # Prepend context
        x = np.concatenate((self._context, x), axis=1)

        ort_inputs = {
            "input": x.astype(np.float32),
            "state": self._state,
            "sr": np.array(SAMPLE_RATE, dtype=np.int64),
        }
        out, self._state = self.session.run(None, ort_inputs)

        # Update context (keep last 64 samples)
        self._context = x[:, -self.CONTEXT_SIZE :]
        self._maybe_reset()

        return float(out[0][0])

    def reset(self) -> None:
        """Force-reset internal state (call between turns)."""
        self._init_states()


# ═════════════════════════════════════════════════════════════════════════════
#  SMART TURN v3 — ML end-of-turn detection
# ═════════════════════════════════════════════════════════════════════════════
class SmartTurnInference:
    """
    Smart Turn v3.2 ONNX inference.
    Takes 16 kHz mono float32 audio (up to 8 seconds).
    Returns {'prediction': 0|1, 'probability': float}.
    """

    def __init__(self, model_path: str | Path | None = None, cpu_count: int = 1):
        path = str(model_path or _ensure_model(SMART_TURN_PATH, SMART_TURN_URL))

        so = ort.SessionOptions()
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.inter_op_num_threads = cpu_count
        so.intra_op_num_threads = max(cpu_count, 2)
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self.session = ort.InferenceSession(
            path, providers=["CPUExecutionProvider"], sess_options=so,
        )

        # Load WhisperFeatureExtractor (Whisper Tiny backbone)
        from transformers import WhisperFeatureExtractor
        self.feature_extractor = WhisperFeatureExtractor(chunk_length=MAX_TURN_SECS)

        log.info("Smart Turn v3.2 loaded  ✓  (cpu_count=%d)", cpu_count)

    def predict(self, audio_f32: np.ndarray) -> dict:
        """
        Predict end-of-turn on audio.

        Args:
            audio_f32: 16 kHz mono float32 numpy array (any length).

        Returns:
            {'prediction': 1 (complete) | 0 (incomplete),
             'probability': float (sigmoid probability of completion)}
        """
        # Truncate to last 8 seconds or pad with leading zeros
        audio = _truncate_or_pad(audio_f32, n_seconds=MAX_TURN_SECS)

        # Extract Whisper features
        inputs = self.feature_extractor(
            audio,
            sampling_rate=SAMPLE_RATE,
            return_tensors="np",
            padding="max_length",
            max_length=MAX_TURN_SECS * SAMPLE_RATE,
            truncation=True,
            do_normalize=True,
        )

        features = inputs.input_features.squeeze(0).astype(np.float32)
        features = np.expand_dims(features, axis=0)  # batch dim

        # ONNX inference
        t0 = time.perf_counter()
        outputs = self.session.run(None, {"input_features": features})
        dt_ms = (time.perf_counter() - t0) * 1000.0

        probability = float(outputs[0][0].item())
        prediction = 1 if probability > 0.5 else 0

        log.debug(
            "Smart Turn inference: prob=%.4f → %s  (%.1f ms)",
            probability,
            "Complete" if prediction else "Incomplete",
            dt_ms,
        )

        return {
            "prediction": prediction,
            "probability": probability,
            "inference_ms": round(dt_ms, 2),
        }


# ═════════════════════════════════════════════════════════════════════════════
#  UTILITIES
# ═════════════════════════════════════════════════════════════════════════════
def _truncate_or_pad(
    audio: np.ndarray, n_seconds: int = MAX_TURN_SECS
) -> np.ndarray:
    """Truncate to last n_seconds or zero-pad at the beginning."""
    max_samples = n_seconds * SAMPLE_RATE
    if len(audio) > max_samples:
        return audio[-max_samples:]
    elif len(audio) < max_samples:
        padding = max_samples - len(audio)
        return np.pad(audio, (padding, 0), mode="constant", constant_values=0)
    return audio


def _ensure_model(path: Path, url: str) -> Path:
    """Download model if not present."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        log.info("Downloading %s …", path.name)
        urllib.request.urlretrieve(url, str(path))
        size_mb = path.stat().st_size / (1024 * 1024)
        log.info("Downloaded %s  (%.1f MB)  ✓", path.name, size_mb)
    return path
