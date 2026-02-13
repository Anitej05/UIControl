"""
GestureHandler — Maps gesture events to WindowsControl tool executions.
Translates normalised (0-1) gesture coordinates to screen pixel actions.
Also provides continuous cursor tracking so the OS mouse follows the hand.
"""

import logging
import time
from typing import Optional

import pyautogui

from tools.base import BaseTool, ToolResult, ToolResultStatus
from config import SCREEN_WIDTH, SCREEN_HEIGHT

# Disable pyautogui fail-safe for fluid cursor tracking
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0

log = logging.getLogger("gesture_handler")


class GestureHandler:
    """
    Receives parsed gesture event dicts (from the WebSocket stream)
    and dispatches the appropriate tool call on the shared BaseTool layers.

    Gesture → Action mapping:
        tap            → click(x, y)
        double_tap     → click(x, y, clicks=2)
        pinch_hold     → click(x, y, button='right')
        pinch_drag     → drag_and_drop(origin → current)
        pinch_flick    → scroll(direction from velocity)
    """

    # Gesture types we handle
    HANDLED_GESTURES = {"tap", "double_tap", "pinch_hold", "pinch_drag", "pinch_flick"}

    # ── Cursor tuning ────────────────────────────────────────────────────
    # EMA smoothing: 0.0 = frozen, 1.0 = raw (no smoothing).
    # Lower values = smoother but laggier. 0.3-0.4 is a good balance.
    SMOOTHING_FACTOR = 0.35

    # Edge margin: how much of the normalised range near the edges to
    # discard and stretch.  0.08 means the usable hand area (0.08 – 0.92)
    # is remapped to the full screen (0.0 – 1.0) so you can comfortably
    # reach all four edges without extreme hand positions.
    EDGE_MARGIN = 0.08
    # ─────────────────────────────────────────────────────────────────────

    def __init__(self, tool_sets: list[BaseTool], screen_w: int = None, screen_h: int = None):
        self._tool_sets = tool_sets
        self._tools: dict[str, BaseTool] = {}
        self._screen_w = screen_w or SCREEN_WIDTH
        self._screen_h = screen_h or SCREEN_HEIGHT

        # Build a tool_name → tool_set lookup (same as Agent does)
        for tool_set in tool_sets:
            for defn in tool_set.get_definitions():
                self._tools[defn.name] = tool_set

        self._action_count = 0
        self._last_cursor_log = 0.0  # Rate-limit cursor movement logging

        # Smoothed cursor state (initialised on first event)
        self._smooth_x: Optional[float] = None
        self._smooth_y: Optional[float] = None

        # Cursor freeze: lock position during active gestures to prevent
        # hand drift from moving the click target.
        self._frozen = False
        self._freeze_px: int = 0
        self._freeze_py: int = 0

    # ── Public API ───────────────────────────────────────────────────────

    def _remap_edge(self, v: float) -> float:
        """Remap normalised value so the inner range fills the full screen."""
        m = self.EDGE_MARGIN
        return max(0.0, min(1.0, (v - m) / (1.0 - 2 * m)))

    def _smooth(self, raw_x: float, raw_y: float) -> tuple[float, float]:
        """Apply EMA smoothing to reduce jitter."""
        a = self.SMOOTHING_FACTOR
        if self._smooth_x is None:
            # First frame — initialise to current position
            self._smooth_x = raw_x
            self._smooth_y = raw_y
        else:
            self._smooth_x = a * raw_x + (1 - a) * self._smooth_x
            self._smooth_y = a * raw_y + (1 - a) * self._smooth_y
        return self._smooth_x, self._smooth_y

    def handle_cursor(self, event: dict):
        """
        Move the OS mouse cursor to track the hand position.
        Called on EVERY frame (not just gesture events) for smooth tracking.
        Uses pyautogui directly for zero-latency cursor movement.
        Applies edge remapping + EMA smoothing for comfort.

        When a gesture is active (pinch in progress), the cursor is FROZEN
        at its pre-pinch position to prevent drift during the action.
        """
        # Check gesture state to decide whether to freeze/unfreeze
        gesture = event.get("gesture", {})
        gstate = gesture.get("state", "")
        gtype = gesture.get("type", "none")

        if gstate in ("start", "active") and gtype != "none":
            # Gesture in progress — freeze cursor at current position
            if not self._frozen:
                self._frozen = True
                # Capture current pixel position as the freeze point
                cursor = event.get("cursor")
                if cursor:
                    cx = self._remap_edge(cursor.get("x", 0.5))
                    cy = self._remap_edge(cursor.get("y", 0.5))
                    cx, cy = self._smooth(cx, cy)
                    self._freeze_px, self._freeze_py = self._norm_to_px(cx, cy)
                else:
                    # Fallback: use current mouse position
                    pos = pyautogui.position()
                    self._freeze_px, self._freeze_py = pos.x, pos.y
                log.debug("Cursor FROZEN at (%d, %d)", self._freeze_px, self._freeze_py)
            return  # Don't move cursor while frozen

        if gstate in ("end", "ended", "") or gtype == "none":
            if self._frozen:
                self._frozen = False
                log.debug("Cursor UNFROZEN")

        cursor = event.get("cursor")
        if not cursor:
            return

        # Raw normalised coords from the gesture client
        cx = cursor.get("x", 0.5)
        cy = cursor.get("y", 0.5)

        # 1. Remap edges so you can reach all corners comfortably
        cx = self._remap_edge(cx)
        cy = self._remap_edge(cy)

        # 2. Smooth to reduce jitter
        cx, cy = self._smooth(cx, cy)

        # 3. Convert to pixels and move
        px, py = self._norm_to_px(cx, cy)

        try:
            pyautogui.moveTo(px, py, duration=0, _pause=False)
        except Exception:
            pass  # Swallow errors to avoid breaking the event loop

        # Rate-limited logging (once per second max)
        now = time.monotonic()
        if now - self._last_cursor_log > 1.0:
            log.debug("Cursor → (%d, %d)", px, py)
            self._last_cursor_log = now

    def handle_event(self, event: dict) -> Optional[ToolResult]:
        """
        Process a full gesture event envelope.
        Returns the ToolResult if an action was executed, None if the event was skipped.
        """
        gesture = event.get("gesture")
        if not gesture:
            return None

        gtype = gesture.get("type", "none")
        gstate = gesture.get("state", "")

        # Only act on completed gestures to avoid double-firing.
        # The gesture client uses "end" (not "ended") when a gesture completes.
        if gstate not in ("end", "ended"):
            return None

        if gtype not in self.HANDLED_GESTURES:
            return None

        # Extract data we need
        interaction = gesture.get("interaction_data", {})
        movement = interaction.get("movement", {})
        cursor = event.get("cursor", {})

        # If we froze the cursor during the gesture, use the frozen pixel
        # position instead of the (possibly drifted) current position.
        if self._frozen or (self._freeze_px and self._freeze_py):
            # Use frozen pixel coords — convert back to normalised for handlers
            cx = self._freeze_px / self._screen_w
            cy = self._freeze_py / self._screen_h
        else:
            cx = cursor.get("x", gesture.get("tracking_data", {}).get("world_coordinates", {}).get("x", 0.5))
            cy = cursor.get("y", gesture.get("tracking_data", {}).get("world_coordinates", {}).get("y", 0.5))

        # Dispatch to the handler
        handler_map = {
            "tap": self._handle_tap,
            "double_tap": self._handle_double_tap,
            "pinch_hold": self._handle_pinch_hold,
            "pinch_drag": self._handle_pinch_drag,
            "pinch_flick": self._handle_pinch_flick,
        }

        handler = handler_map.get(gtype)
        if handler is None:
            return None

        self._action_count += 1
        log.info(
            "Gesture #%d: %s at (%.3f, %.3f)",
            self._action_count, gtype, cx, cy,
        )

        result = handler(cx, cy, gesture, interaction, movement)

        # Clear freeze point after action so next idle doesn't reuse stale coords
        self._freeze_px = 0
        self._freeze_py = 0

        return result

    def handle_speech(self, event: dict) -> Optional[str]:
        """
        Process a speech event. Returns the transcript text if it's a final transcript,
        None otherwise. (Speech command execution is reserved for future implementation.)
        """
        speech = event.get("speech")
        if not speech:
            return None

        stype = speech.get("type", "")
        sstate = speech.get("state", "")
        data = speech.get("data", {})

        if stype == "transcript" and sstate == "final":
            text = data.get("text", "")
            confidence = data.get("confidence", 0.0)
            log.info("Speech transcript: \"%s\" (conf=%.2f)", text, confidence)
            return text

        return None

    @property
    def action_count(self) -> int:
        return self._action_count

    # ── Private handlers ─────────────────────────────────────────────────

    def _norm_to_px(self, nx: float, ny: float) -> tuple[int, int]:
        """Convert normalised (0-1) coordinates to screen pixels."""
        px = int(max(0.0, min(1.0, nx)) * self._screen_w)
        py = int(max(0.0, min(1.0, ny)) * self._screen_h)
        return px, py

    def _execute_tool(self, tool_name: str, **kwargs) -> ToolResult:
        """Execute a tool on the shared layer instances."""
        tool_set = self._tools.get(tool_name)
        if not tool_set:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Tool '{tool_name}' not available",
            )
        return tool_set.execute(tool_name, **kwargs)

    def _handle_tap(self, cx, cy, gesture, interaction, movement) -> ToolResult:
        """Single left-click at cursor position."""
        px, py = self._norm_to_px(cx, cy)
        log.info("  → click(%d, %d)", px, py)
        return self._execute_tool("click", x=px, y=py, button="left", clicks=1)

    def _handle_double_tap(self, cx, cy, gesture, interaction, movement) -> ToolResult:
        """Double left-click at cursor position."""
        px, py = self._norm_to_px(cx, cy)
        log.info("  → click(%d, %d, clicks=2)", px, py)
        return self._execute_tool("click", x=px, y=py, button="left", clicks=2)

    def _handle_pinch_hold(self, cx, cy, gesture, interaction, movement) -> ToolResult:
        """Right-click (context menu) at cursor position."""
        px, py = self._norm_to_px(cx, cy)
        log.info("  → click(%d, %d, button=right)", px, py)
        return self._execute_tool("click", x=px, y=py, button="right", clicks=1)

    def _handle_pinch_drag(self, cx, cy, gesture, interaction, movement) -> ToolResult:
        """Drag from origin to current position."""
        origin = movement.get("origin", {})
        current = movement.get("current", {})

        ox, oy = origin.get("x", cx), origin.get("y", cy)
        dx, dy = current.get("x", cx), current.get("y", cy)

        from_x, from_y = self._norm_to_px(ox, oy)
        to_x, to_y = self._norm_to_px(dx, dy)

        log.info("  → drag_and_drop(%d,%d → %d,%d)", from_x, from_y, to_x, to_y)
        return self._execute_tool(
            "drag_and_drop",
            from_x=from_x, from_y=from_y,
            to_x=to_x, to_y=to_y,
            duration=0.5,
        )

    def _handle_pinch_flick(self, cx, cy, gesture, interaction, movement) -> ToolResult:
        """Scroll based on flick velocity vector."""
        velocity = movement.get("velocity", {})
        vx = velocity.get("vx", 0.0)
        vy = velocity.get("vy", 0.0)

        # Determine primary scroll direction from velocity
        if abs(vy) >= abs(vx):
            direction = "down" if vy > 0 else "up"
            magnitude = abs(vy)
        else:
            direction = "right" if vx > 0 else "left"
            magnitude = abs(vx)

        # Scale velocity to scroll amount (1-10 clicks)
        amount = max(1, min(10, int(magnitude / 200)))

        px, py = self._norm_to_px(cx, cy)
        log.info("  → scroll(%d, %d, %s, %d)", px, py, direction, amount)
        return self._execute_tool("scroll", x=px, y=py, direction=direction, amount=amount)
