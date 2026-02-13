"""
Speech Client v5 — Silero VAD + Smart Turn v3 + NVIDIA Parakeet ASR
───────────────────────────────────────────────────────────────────
LOCAL  : Silero VAD (neural VAD) + Smart Turn v3.2 (ML end-of-turn)
API    : NVIDIA NIM Parakeet ASR via gRPC (only called on confirmed EOR)
Audio  : sounddevice 16 kHz mono

Pipeline:
  Mic → 512-sample chunks → Silero VAD (LOCAL, speech probability)
       → Speech? Accumulate full turn audio
       → Silence? → Smart Turn v3 (LOCAL, EOR decision)
       → EOR confirmed? → gRPC call → NVIDIA Parakeet ASR (API)
       → Transcript → WebSocket event + HTTP POST

Events streamed to  ws://host:8000/ws/speech
Final transcripts   POST → http://host:8000/api/speech
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timezone

import numpy as np
import orjson
import sounddevice as sd
import websockets
import httpx

# ═════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════
WS_URL           = "ws://localhost:8000/ws/speech"
API_URL          = "http://localhost:8000/api/speech"
RECONNECT_DELAY  = 2.0

# ── NVIDIA Parakeet ASR (gRPC cloud API) ────────────────────────────────
NVIDIA_API_KEY   = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_ASR_URL   = os.getenv("NVIDIA_ASR_URL", "grpc.nvcf.nvidia.com:443")
NVIDIA_FUNC_ID   = os.getenv(
    "NVIDIA_FUNC_ID",
    "1598d209-5e27-4d3c-8079-4751568b1081",  # parakeet-ctc-1.1b-asr
)
NVIDIA_ASR_LANG  = os.getenv("NVIDIA_ASR_LANG", "en-US")

# ── Audio settings ──────────────────────────────────────────────────────
SAMPLE_RATE      = 16_000          # Silero & Smart Turn expect 16 kHz
CHANNELS         = 1               # mono
CHUNK_SIZE       = 512             # Silero VAD native chunk size at 16 kHz

# ── Silero VAD settings ────────────────────────────────────────────────
VAD_THRESHOLD    = 0.5             # speech probability threshold
PRE_SPEECH_MS    = 200             # ms of audio to keep before speech trigger
PRE_SPEECH_CHUNKS = math.ceil(PRE_SPEECH_MS / ((CHUNK_SIZE / SAMPLE_RATE) * 1000))

# ── Smart Turn EOR settings (optimized for peak accuracy) ──────────────
SMART_TURN_STOP_MS      = 200      # ms silence before running Smart Turn
SMART_TURN_STOP_CHUNKS  = math.ceil(SMART_TURN_STOP_MS / ((CHUNK_SIZE / SAMPLE_RATE) * 1000))
SMART_TURN_PROB_THRESH  = 0.5      # probability threshold for "complete" turn
HARD_SILENCE_SECS       = 3.0      # force EOR after this much silence
HARD_SILENCE_CHUNKS     = math.ceil(HARD_SILENCE_SECS / (CHUNK_SIZE / SAMPLE_RATE))
MAX_TURN_SECS           = 8        # Smart Turn max input length
MIN_SPEECH_SECS         = 0.3      # ignore bursts shorter than this

# ── Logging ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("speech-client")


# ═════════════════════════════════════════════════════════════════════════════
#  NVIDIA PARAKEET ASR — gRPC CLIENT (one-shot offline recognition)
# ═════════════════════════════════════════════════════════════════════════════
_riva_asr = None  # lazy-loaded riva.client.ASRService

def _get_riva_asr():
    """Lazy-init the Riva gRPC ASR client (only once)."""
    global _riva_asr
    if _riva_asr is not None:
        return _riva_asr

    import riva.client

    log.info("Connecting to NVIDIA Parakeet ASR → %s …", NVIDIA_ASR_URL)

    # Build metadata for cloud function routing
    metadata = [
        ("function-id", NVIDIA_FUNC_ID),
        ("authorization", f"Bearer {NVIDIA_API_KEY}"),
    ]

    auth = riva.client.Auth(
        ssl_root_cert=None,
        use_ssl=True,
        uri=NVIDIA_ASR_URL,
        metadata_args=metadata,
    )
    _riva_asr = riva.client.ASRService(auth)
    log.info("NVIDIA Parakeet ASR client ready  ✓")
    return _riva_asr


def transcribe_nvidia_sync(audio_f32: np.ndarray) -> dict:
    """
    Send audio to NVIDIA Parakeet ASR via gRPC and return transcript.

    Args:
        audio_f32: 16 kHz mono float32 audio array

    Returns:
        {'text': str, 'confidence': float, 'language': str}
    """
    import riva.client

    asr = _get_riva_asr()

    # Convert float32 → 16-bit PCM bytes (Riva expects LINEAR16 raw bytes)
    audio_int16 = np.clip(audio_f32, -1.0, 1.0)
    audio_int16 = (audio_int16 * 32767).astype(np.int16)
    audio_bytes = audio_int16.tobytes()

    # Build recognition config
    config = riva.client.RecognitionConfig(
        encoding=riva.client.AudioEncoding.LINEAR_PCM,
        sample_rate_hertz=SAMPLE_RATE,
        language_code=NVIDIA_ASR_LANG,
        max_alternatives=1,
        enable_automatic_punctuation=True,
        audio_channel_count=1,
    )

    t0 = time.perf_counter()
    try:
        response = asr.offline_recognize(audio_bytes, config)
        dt_ms = (time.perf_counter() - t0) * 1000.0

        # Parse response
        text = ""
        confidence = 0.0
        for result in response.results:
            if result.alternatives:
                alt = result.alternatives[0]
                text += alt.transcript
                confidence = max(confidence, alt.confidence)

        text = text.strip()
        log.info(
            '📝  NVIDIA ASR: "%s"  (%.0f ms, conf=%.2f)',
            text, dt_ms, confidence,
        )
        return {
            "text": text,
            "confidence": confidence,
            "language": NVIDIA_ASR_LANG,
        }

    except Exception as exc:
        dt_ms = (time.perf_counter() - t0) * 1000.0
        log.error("NVIDIA ASR gRPC call failed: %s  (%.0f ms)", exc, dt_ms)
        return {"text": "", "confidence": 0.0, "language": NVIDIA_ASR_LANG}


# ═════════════════════════════════════════════════════════════════════════════
#  EVENT BUILDER
# ═════════════════════════════════════════════════════════════════════════════
def make_event(
    event_type: str,
    state: str,
    text: str = "",
    confidence: float = 0.0,
    duration_ms: float = 0.0,
    language: str = "",
) -> dict:
    return {
        "event_id":  f"evt_{uuid.uuid4().hex[:12]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "speech": {
            "type":  event_type,
            "state": state,
            "data": {
                "text":        text,
                "confidence":  round(float(confidence), 4),
                "duration_ms": round(float(duration_ms), 1),
                "language":    language,
            },
        },
    }


# ═════════════════════════════════════════════════════════════════════════════
#  SPEECH ENGINE — Silero VAD + Smart Turn EOR (all local)
# ═════════════════════════════════════════════════════════════════════════════
class SpeechEngine:
    """
    Pipeline (all LOCAL except ASR API call):
    1. Silero VAD detects speech/silence per 512-sample chunk (LOCAL)
    2. On speech → accumulate full turn audio
    3. On silence → Smart Turn v3 predicts end-of-turn (LOCAL)
    4. On EOR → return audio for API transcription
    """

    def __init__(self):
        from smart_turn_inference import SileroVAD, SmartTurnInference

        log.info("Loading local models …")
        self.vad = SileroVAD()
        self.smart_turn = SmartTurnInference()
        log.info("Local models loaded  ✓  (no Whisper — using NVIDIA API)")

        # State
        self.speech_active = False
        self.speech_start = 0.0
        self.trailing_silence = 0
        self.since_trigger = 0
        self.smart_turn_pending = False

        # Audio buffers
        self._pre_buffer: deque[np.ndarray] = deque(maxlen=PRE_SPEECH_CHUNKS)
        self._turn_chunks: list[np.ndarray] = []

    def feed(self, chunk: np.ndarray) -> tuple[str, np.ndarray | None]:
        """
        Feed one 512-sample audio chunk.

        Returns:
            (event_type, audio_or_none)
            - ("speaking", None)    → speech just started
            - ("eor", audio_array) → end-of-turn, audio ready for API
            - ("skip", None)       → too short, skipped
            - ("", None)           → nothing interesting
        """
        speech_prob = self.vad.prob(chunk)
        is_speech = speech_prob > VAD_THRESHOLD

        if not self.speech_active:
            self._pre_buffer.append(chunk.copy())

            if is_speech:
                self.speech_active = True
                self.speech_start = time.monotonic()
                self.trailing_silence = 0
                self.since_trigger = 0
                self.smart_turn_pending = False
                self._turn_chunks = list(self._pre_buffer)
                self._turn_chunks.append(chunk.copy())
                self.since_trigger = 1
                log.info("🎙  Speech started (vad_prob=%.3f)", speech_prob)
                return ("speaking", None)

            return ("", None)

        # Currently in a speech turn
        self._turn_chunks.append(chunk.copy())
        self.since_trigger += 1

        if is_speech:
            self.trailing_silence = 0
            self.smart_turn_pending = False
        else:
            self.trailing_silence += 1

        # Hard silence fallback
        if self.trailing_silence >= HARD_SILENCE_CHUNKS:
            log.info(
                "⏹  Hard silence EOR (%.1fs silence)",
                self.trailing_silence * CHUNK_SIZE / SAMPLE_RATE,
            )
            return self._end_turn()

        # Smart Turn check
        if self.trailing_silence >= SMART_TURN_STOP_CHUNKS and not self.smart_turn_pending:
            self.smart_turn_pending = True
            turn_audio = np.concatenate(self._turn_chunks).astype(np.float32)
            speech_dur = time.monotonic() - self.speech_start

            if speech_dur < MIN_SPEECH_SECS:
                return ("", None)

            result = self.smart_turn.predict(turn_audio)
            prob = result["probability"]
            inference_ms = result["inference_ms"]

            if result["prediction"] == 1:
                log.info(
                    "🧠  Smart Turn: prob=%.4f → Complete  (%.1f ms)",
                    prob, inference_ms,
                )
                return self._end_turn()
            else:
                log.info(
                    "🧠  Smart Turn: prob=%.4f → Incomplete, keep listening  (%.1f ms)",
                    prob, inference_ms,
                )

        if is_speech and self.smart_turn_pending:
            self.smart_turn_pending = False

        return ("", None)

    def _end_turn(self) -> tuple[str, np.ndarray | None]:
        """Finalize turn: return audio for API transcription."""
        speech_dur = time.monotonic() - self.speech_start
        self.speech_active = False
        trailing = self.trailing_silence
        self.trailing_silence = 0
        self.since_trigger = 0
        self.smart_turn_pending = False

        if speech_dur < MIN_SPEECH_SECS:
            log.info("⚡  Too short (%.0f ms), skipping", speech_dur * 1000)
            self._turn_chunks.clear()
            self._pre_buffer.clear()
            self.vad.reset()
            return ("skip", None)

        log.info(
            "⏹  EOR — %.1fs speech → sending to NVIDIA Parakeet ASR …",
            speech_dur,
        )

        audio = np.concatenate(self._turn_chunks).astype(np.float32)
        self._turn_chunks.clear()

        # Trim trailing silence — keep only 0.2s after last speech
        trim_samples = max(0, int(trailing * CHUNK_SIZE) - int(SAMPLE_RATE * 0.2))
        if trim_samples > 0 and len(audio) > trim_samples:
            audio = audio[:-trim_samples]

        self.vad.reset()
        self._pre_buffer.clear()

        return ("eor", audio)


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN LOOP
# ═════════════════════════════════════════════════════════════════════════════
async def run():
    engine = SpeechEngine()

    # ── Validate NVIDIA API key ──────────────────────────────────────
    if not NVIDIA_API_KEY:
        log.error(
            "NVIDIA_API_KEY not set! Get one from https://build.nvidia.com"
        )
        sys.exit(1)

    # ── Pre-connect NVIDIA ASR (lazy, but warm up now) ───────────────
    _get_riva_asr()

    # ── WebSocket connection ─────────────────────────────────────────
    ws = None
    drain = None

    async def _drain(s):
        try:
            async for _ in s:
                pass
        except Exception:
            pass

    async def connect_ws():
        nonlocal ws, drain
        if drain and not drain.done():
            drain.cancel()
        while ws is None:
            try:
                ws = await websockets.connect(
                    WS_URL,
                    max_size=2 ** 20,
                    ping_interval=20, ping_timeout=10, close_timeout=5,
                )
                drain = asyncio.create_task(_drain(ws))
                log.info("WS → %s  ✓", WS_URL)
            except Exception as e:
                log.warning("WS connect fail (%s), retry %.0fs", e, RECONNECT_DELAY)
                await asyncio.sleep(RECONNECT_DELAY)

    await connect_ws()

    # ── HTTP client for server API ───────────────────────────────────
    http = httpx.AsyncClient(timeout=30.0)

    # ── Audio queue ──────────────────────────────────────────────────
    audio_queue: asyncio.Queue[np.ndarray] = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def audio_callback(indata, frames, time_info, status):
        if status:
            log.warning("Audio: %s", status)
        loop.call_soon_threadsafe(audio_queue.put_nowait, indata[:, 0].copy())

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        blocksize=CHUNK_SIZE,
        dtype="float32",
        callback=audio_callback,
    )
    stream.start()
    log.info(
        "🎤  Microphone ready  (%d Hz, %d-sample chunks, %.1f ms/chunk)",
        SAMPLE_RATE, CHUNK_SIZE, CHUNK_SIZE / SAMPLE_RATE * 1000,
    )

    # ── Helper to send over WebSocket ────────────────────────────────
    async def ws_send(data: bytes):
        nonlocal ws
        try:
            await ws.send(data)
        except Exception:
            log.warning("WS send fail, reconnecting …")
            try:
                await ws.close()
            except Exception:
                pass
            ws = None
            await connect_ws()
            try:
                await ws.send(data)
            except Exception:
                pass

    # ── Ready ────────────────────────────────────────────────────────
    await ws_send(orjson.dumps(make_event("status", "listening")))
    log.info("✅  Ready — start speaking!")
    log.info("    VAD      : Silero (local)")
    log.info("    EOR      : Smart Turn v3 (local)")
    log.info("    ASR      : NVIDIA Parakeet (gRPC → %s)", NVIDIA_ASR_URL)

    # ── Main recognition loop ────────────────────────────────────────
    try:
        while True:
            chunk = await audio_queue.get()
            event_type, audio = engine.feed(chunk)

            if event_type == "speaking":
                await ws_send(orjson.dumps(make_event("status", "speaking")))

            elif event_type == "skip":
                await ws_send(orjson.dumps(make_event("status", "listening")))

            elif event_type == "eor" and audio is not None:
                # ═══ API CALL — run gRPC in a thread to avoid blocking ═══
                speech_dur = len(audio) / SAMPLE_RATE
                result = await asyncio.get_event_loop().run_in_executor(
                    None, transcribe_nvidia_sync, audio
                )
                text = result["text"]

                if text:
                    event = make_event(
                        "transcript", "final",
                        text=text,
                        confidence=result["confidence"],
                        duration_ms=speech_dur * 1000,
                        language=result["language"],
                    )
                    raw = orjson.dumps(event)
                    await ws_send(raw)

                    # POST to local server API
                    try:
                        resp = await http.post(API_URL, json=event["speech"]["data"])
                        log.info("📡  API POST → %d", resp.status_code)
                    except Exception as exc:
                        log.warning("API POST failed: %s", exc)
                else:
                    log.info("📝  Empty transcript, skipping")

                # Back to listening
                await ws_send(orjson.dumps(make_event("status", "listening")))
                log.info("👂  Listening …")

    except KeyboardInterrupt:
        log.info("Interrupted by user")
    finally:
        stream.stop()
        stream.close()
        if drain and not drain.done():
            drain.cancel()
        if ws:
            try:
                await ws.close()
            except Exception:
                pass
        await http.aclose()
        log.info("Done.")


# ═════════════════════════════════════════════════════════════════════════════
#  ENTRYPOINT
# ═════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("╔════════════════════════════════════════════════════════════╗")
    print("║  Speech Client v5 — Smart Turn + NVIDIA Parakeet ASR      ║")
    print("║                                                           ║")
    print("║  LOCAL : Silero VAD + Smart Turn v3.2 (all on-device)    ║")
    print("║  API   : NVIDIA Parakeet ASR (gRPC, only on EOR)        ║")
    print("║  Audio : 16 kHz mono, 512-sample chunks (32 ms)         ║")
    print("║                                                           ║")
    print("║  Press Ctrl+C to quit                                     ║")
    print("╚════════════════════════════════════════════════════════════╝")
    asyncio.run(run())
