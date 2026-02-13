"""
Voice & Gesture WebSocket Server  (v3 — Speech + Gesture)
──────────────────────────────────────────────────────────
FastAPI + Uvicorn + orjson
Optional Redis pub/sub for multi-instance broadcasting.
Falls back to in-memory fan-out when Redis is unavailable.

Endpoints:
  ws://host:8000/ws/gestures   — gesture events
  ws://host:8000/ws/speech     — speech events (ASR/STT/EOR)
  POST /api/speech             — receive final transcriptions
  GET  /health                 — system health
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Set

import orjson
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from starlette.websockets import WebSocketState

# ─── Configuration ───────────────────────────────────────────────────────────
REDIS_URL = "redis://localhost:6379/0"
REDIS_CHANNEL_GESTURE = "gestures:broadcast"
REDIS_CHANNEL_SPEECH  = "speech:broadcast"
HOST = "0.0.0.0"
PORT = 8000
GESTURE_REQUIRED_KEYS = {"event_id", "timestamp", "gesture"}
SPEECH_REQUIRED_KEYS  = {"event_id", "timestamp", "speech"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("server")


# ─── Connection Manager ─────────────────────────────────────────────────────
class ConnectionManager:
    """
    Track live WebSocket clients and fan-out messages.
    Uses Redis pub/sub when available; falls back to in-memory broadcast.

    IMPORTANT: broadcast excludes the sender to prevent receive-buffer overflow
    on producer clients that only send (never read).
    """

    def __init__(self, name: str, redis_channel: str) -> None:
        self._name = name
        self._redis_channel = redis_channel
        self._clients: Set[WebSocket] = set()
        self._redis = None
        self._pubsub = None
        self._listener_task: asyncio.Task | None = None
        self._use_redis: bool = False
        self._msg_count: int = 0

    # ── Lifecycle ────────────────────────────────────────────────────────
    async def startup(self) -> None:
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(REDIS_URL, decode_responses=False)
            await self._redis.ping()
            self._use_redis = True
            log.info("[%s] Redis connected  ✓  (%s)", self._name, REDIS_URL)

            self._pubsub = self._redis.pubsub()
            await self._pubsub.subscribe(self._redis_channel)
            self._listener_task = asyncio.create_task(self._redis_listener())
            log.info("[%s] Subscribed to channel '%s'", self._name, self._redis_channel)

        except Exception as exc:
            self._use_redis = False
            self._redis = None
            log.warning(
                "[%s] Redis unavailable (%s). Using in-memory broadcast.",
                self._name, exc,
            )

    async def shutdown(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._pubsub:
            try:
                await self._pubsub.unsubscribe(self._redis_channel)
                await self._pubsub.close()
            except Exception:
                pass
        if self._redis:
            try:
                await self._redis.close()
            except Exception:
                pass
        log.info("[%s] Shutdown complete  ✓", self._name)

    # ── Client management ────────────────────────────────────────────────
    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        log.info("[%s] Client connected   (%d total)", self._name, len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        log.info("[%s] Client disconnected (%d total)", self._name, len(self._clients))

    @property
    def client_count(self) -> int:
        return len(self._clients)

    # ── Ingest (sender-excluded broadcast) ───────────────────────────────
    async def ingest(self, raw: bytes, sender: WebSocket) -> None:
        """Validate, log, and broadcast — excluding the sender."""
        self._msg_count += 1

        if self._use_redis and self._redis:
            await self._redis.publish(self._redis_channel, raw)
        else:
            await self._broadcast_local(raw, exclude=sender)

    # ── Broadcast to all (no exclusion — for server-originated events) ───
    async def broadcast_all(self, raw: bytes) -> None:
        """Broadcast to ALL connected clients (used for API-injected events)."""
        await self._broadcast_local(raw, exclude=None)

    # ── Redis listener ───────────────────────────────────────────────────
    async def _redis_listener(self) -> None:
        assert self._pubsub is not None
        async for message in self._pubsub.listen():
            if message["type"] != "message":
                continue
            data: bytes = message["data"]
            await self._broadcast_local(data, exclude=None)

    # ── Local broadcast ──────────────────────────────────────────────────
    async def _broadcast_local(
        self, data: bytes, *, exclude: WebSocket | None = None
    ) -> None:
        """Send to all local clients except `exclude`. Drop dead sockets."""
        targets = [ws for ws in self._clients if ws is not exclude]
        if not targets:
            return

        async def _safe_send(ws: WebSocket) -> WebSocket | None:
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_bytes(data)
                    return None
                else:
                    return ws  # stale
            except Exception:
                return ws  # stale

        results = await asyncio.gather(*[_safe_send(ws) for ws in targets])
        for stale in results:
            if stale is not None:
                self._clients.discard(stale)


gesture_manager = ConnectionManager("gesture", REDIS_CHANNEL_GESTURE)
speech_manager  = ConnectionManager("speech",  REDIS_CHANNEL_SPEECH)


# ─── FastAPI App ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await gesture_manager.startup()
    await speech_manager.startup()
    log.info("Server is READY")
    log.info("  ws://localhost:%d/ws/gestures", PORT)
    log.info("  ws://localhost:%d/ws/speech", PORT)
    log.info("  POST http://localhost:%d/api/speech", PORT)
    yield
    await gesture_manager.shutdown()
    await speech_manager.shutdown()


app = FastAPI(
    title="Voice & Gesture Control Server",
    version="3.0.0",
    lifespan=lifespan,
)


# ─── Lightweight validation ─────────────────────────────────────────────────
def validate_gesture(raw: bytes) -> bytes | None:
    """Check required keys exist. Return raw bytes untouched if valid."""
    try:
        obj = orjson.loads(raw)
    except orjson.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if not GESTURE_REQUIRED_KEYS.issubset(obj):
        return None
    return raw


def validate_speech(raw: bytes) -> bytes | None:
    """Check required keys exist. Return raw bytes untouched if valid."""
    try:
        obj = orjson.loads(raw)
    except orjson.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if not SPEECH_REQUIRED_KEYS.issubset(obj):
        return None
    return raw


# ─── WebSocket Endpoints ────────────────────────────────────────────────────
@app.websocket("/ws/gestures")
async def ws_gestures(ws: WebSocket) -> None:
    await gesture_manager.connect(ws)
    try:
        while True:
            message = await ws.receive()

            if "bytes" in message and message["bytes"]:
                raw = message["bytes"]
            elif "text" in message and message["text"]:
                raw = message["text"].encode("utf-8")
            else:
                continue

            validated = validate_gesture(raw)
            if validated is None:
                continue

            await gesture_manager.ingest(validated, sender=ws)

    except WebSocketDisconnect:
        gesture_manager.disconnect(ws)
    except Exception as exc:
        log.warning("Gesture WS error: %s", exc)
        gesture_manager.disconnect(ws)


@app.websocket("/ws/speech")
async def ws_speech(ws: WebSocket) -> None:
    await speech_manager.connect(ws)
    try:
        while True:
            message = await ws.receive()

            if "bytes" in message and message["bytes"]:
                raw = message["bytes"]
            elif "text" in message and message["text"]:
                raw = message["text"].encode("utf-8")
            else:
                continue

            validated = validate_speech(raw)
            if validated is None:
                continue

            await speech_manager.ingest(validated, sender=ws)

    except WebSocketDisconnect:
        speech_manager.disconnect(ws)
    except Exception as exc:
        log.warning("Speech WS error: %s", exc)
        speech_manager.disconnect(ws)


# ─── REST API — Speech Transcript Receiver ───────────────────────────────────
class SpeechPayload(BaseModel):
    text: str
    confidence: float = 0.0
    language: str = "en"
    duration_ms: float = 0.0


@app.post("/api/speech")
async def receive_speech(payload: SpeechPayload):
    """
    Receive a final transcription from the speech client (or any external source).
    Broadcasts the transcript to all /ws/speech subscribers.
    """
    import uuid
    from datetime import datetime, timezone

    event = {
        "event_id":  f"evt_{uuid.uuid4().hex[:12]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "speech": {
            "type":  "transcript",
            "state": "final",
            "data": {
                "text":        payload.text,
                "confidence":  round(payload.confidence, 4),
                "duration_ms": round(payload.duration_ms, 1),
                "language":    payload.language,
            },
        },
    }

    raw = orjson.dumps(event)
    await speech_manager.broadcast_all(raw)

    log.info('📝  Transcript received: "%s"  [conf=%.2f]', payload.text, payload.confidence)

    return {
        "status": "ok",
        "event_id": event["event_id"],
        "text": payload.text,
    }


# ─── Health check ────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "gesture": {
            "clients": gesture_manager.client_count,
            "messages_processed": gesture_manager._msg_count,
            "redis": gesture_manager._use_redis,
        },
        "speech": {
            "clients": speech_manager.client_count,
            "messages_processed": speech_manager._msg_count,
            "redis": speech_manager._use_redis,
        },
    }


# ─── Entrypoint ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host=HOST,
        port=PORT,
        log_level="info",
        ws="websockets",
        loop="asyncio",
    )
