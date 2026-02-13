"""
WebSocket Client — Connects to the Voice & Gesture Control server
and streams gesture/speech events to the GestureHandler.
"""

import asyncio
import json
import logging
from typing import Optional, Callable

log = logging.getLogger("ws_client")


class GestureWSClient:
    """
    Async WebSocket consumer for the gesture + speech server.
    Connects to ws://host:port/ws/gestures and /ws/speech,
    parses incoming JSON events, and dispatches them to callbacks.

    Usage:
        client = GestureWSClient(gesture_url, speech_url)
        await client.run(on_gesture=handler.handle_event, on_speech=handler.handle_speech)
    """

    def __init__(
        self,
        gesture_url: str,
        speech_url: str,
        reconnect_delay: float = 2.0,
        max_reconnect_delay: float = 30.0,
    ):
        self._gesture_url = gesture_url
        self._speech_url = speech_url
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def run(
        self,
        on_gesture: Callable[[dict], None] = None,
        on_speech: Callable[[dict], None] = None,
    ):
        """
        Start listening on both WebSocket endpoints.
        Blocks until stop() is called or KeyboardInterrupt.
        """
        self._running = True

        # Always start gesture listener
        self._tasks.append(
            asyncio.create_task(
                self._listen_loop(self._gesture_url, "gesture", on_gesture)
            )
        )

        # Start speech listener if callback provided
        if on_speech:
            self._tasks.append(
                asyncio.create_task(
                    self._listen_loop(self._speech_url, "speech", on_speech)
                )
            )

        log.info("WebSocket client started (gesture=%s, speech=%s)",
                 self._gesture_url, self._speech_url if on_speech else "disabled")

        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        """Gracefully shut down all listeners."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        log.info("WebSocket client stopped")

    async def _listen_loop(
        self,
        url: str,
        channel: str,
        callback: Optional[Callable[[dict], None]],
    ):
        """
        Connect → listen → reconnect loop for a single WebSocket endpoint.
        Uses exponential backoff on connection failures.
        """
        try:
            import websockets
        except ImportError:
            log.error("websockets package not installed. Run: pip install websockets")
            return

        delay = self._reconnect_delay

        while self._running:
            try:
                log.info("[%s] Connecting to %s ...", channel, url)
                async with websockets.connect(
                    url,
                    max_size=2**20,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    log.info("[%s] Connected ✓", channel)
                    delay = self._reconnect_delay  # Reset backoff on success

                    async for message in ws:
                        if not self._running:
                            break

                        # Parse JSON
                        try:
                            if isinstance(message, bytes):
                                event = json.loads(message.decode("utf-8"))
                            else:
                                event = json.loads(message)
                        except (json.JSONDecodeError, UnicodeDecodeError) as e:
                            log.warning("[%s] Bad message: %s", channel, e)
                            continue

                        # Dispatch to callback
                        if callback:
                            try:
                                callback(event)
                            except Exception as e:
                                log.error("[%s] Callback error: %s", channel, e)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                if not self._running:
                    break
                log.warning(
                    "[%s] Connection lost (%s). Retrying in %.1fs...",
                    channel, e, delay,
                )
                await asyncio.sleep(delay)
                # Exponential backoff
                delay = min(delay * 1.5, self._max_reconnect_delay)

        log.info("[%s] Listener stopped", channel)
