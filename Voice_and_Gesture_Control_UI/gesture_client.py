"""
Vision Client — MediaPipe Hand Gesture Controller  (v4)
───────────────────────────────────────────────────────
Model  : hand_landmarker (float16 — best for real-time)
Engine : Different-finger tap detection (easy & reliable)

Gestures (5 types — EASY to perform):
  ┌──────────────┬───────────────────────────────────────────────┐
  │ tap          │ Thumb touches INDEX FINGER, quick release     │
  │ double_tap   │ Thumb touches MIDDLE FINGER, quick release    │
  │ pinch_hold   │ Thumb touches index finger, HOLD still        │
  │ pinch_drag   │ Thumb touches index finger, MOVE hand         │
  │ pinch_flick  │ Thumb touches index finger, FAST release      │
  └──────────────┴───────────────────────────────────────────────┘

  Index  pinch = tap / hold / drag / flick  (depending on duration & motion)
  Middle pinch = ALWAYS double_tap
"""

from __future__ import annotations

import asyncio
import math
import sys
import time
import urllib.request
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import cv2
import orjson
import websockets

# ─── Windows fix for MediaPipe 0.10.x Tasks API ─────────────────────────────
if sys.platform == "win32":
    import ctypes
    _orig_cdll_getattr = ctypes.CDLL.__getattr__
    def _patched_cdll_getattr(self, name):
        try:
            return _orig_cdll_getattr(self, name)
        except AttributeError:
            if name == "free":
                return ctypes.cdll.msvcrt.free
            raise
    ctypes.CDLL.__getattr__ = _patched_cdll_getattr

import mediapipe as mp

# ═════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════
WS_URL         = "ws://localhost:8000/ws/gestures"
CAMERA_INDEX   = 0
CAM_WIDTH      = 640
CAM_HEIGHT     = 480
TARGET_FPS     = 30
FRAME_INTERVAL = 1.0 / TARGET_FPS
RECONNECT_DELAY = 2.0

# ── Pinch thresholds (normalised coords, 0-1) ───────────────────────────
PINCH_DIST     = 0.04     # Below this → pinched ON.  Above → OFF.
PINCH_EXIT     = 0.05     # Tiny hysteresis to avoid jitter (PINCH_DIST + 0.01)

# ── Gesture timing ──────────────────────────────────────────────────────
TAP_MAX_TIME   = 0.20     # seconds — release before this → Tap, longer → Hold/Drag
HOLD_MIN_MS    = 500      # ms — pinch longer than this, no movement → hold

# ── Movement / velocity ─────────────────────────────────────────────────
DRAG_DEADZONE  = 0.05     # cumulative movement must exceed 5% to count as Drag
FLICK_VELOCITY = 0.30     # distance travelled in last 0.1s must exceed this → Flick

# ── Ghost prevention ────────────────────────────────────────────────────
DOUBLE_TAP_COOL = 0.30    # seconds — after double_tap, ignore index for 0.3s

# ── Model ────────────────────────────────────────────────────────────────
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
)
MODEL_DIR  = Path(__file__).parent / "models"
MODEL_PATH = MODEL_DIR / "hand_landmarker.task"

# ── MediaPipe Tasks API ─────────────────────────────────────────────────
BaseOptions        = mp.tasks.BaseOptions
HandLandmarker     = mp.tasks.vision.HandLandmarker
HandLandmarkerOpts = mp.tasks.vision.HandLandmarkerOptions
VisionRunningMode  = mp.tasks.vision.RunningMode

# Landmark indices
THUMB_TIP  = 4
INDEX_TIP  = 8
MIDDLE_TIP = 12

HAND_CONNS = [
    (0,1),(1,2),(2,3),(3,4),
    (0,5),(5,6),(6,7),(7,8),
    (0,9),(9,10),(10,11),(11,12),
    (0,13),(13,14),(14,15),(15,16),
    (0,17),(17,18),(18,19),(19,20),
    (5,9),(9,13),(13,17),
]

GCOLORS = {
    "tap":         (0, 255, 255),   # yellow
    "double_tap":  (0, 165, 255),   # orange
    "pinch_hold":  (255, 100, 255), # magenta
    "pinch_drag":  (0, 255, 0),     # green
    "pinch_flick": (0, 100, 255),   # red-orange
    "none":        (160, 160, 160), # grey
}

# ── Test overlay settings ────────────────────────────────────────────────
TAP_MARKER_LIFETIME  = 2.0    # seconds to show tap markers
DRAG_TRAIL_LIFETIME  = 2.0    # seconds to show drag trail after release
MARKER_RADIUS        = 12     # crosshair marker size in pixels


def ensure_model() -> str:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    if MODEL_PATH.exists():
        print(f"[INFO] Model: {MODEL_PATH}")
        return str(MODEL_PATH)
    print("[INFO] Downloading hand_landmarker model …")
    urllib.request.urlretrieve(MODEL_URL, str(MODEL_PATH))
    print(f"[INFO] Saved: {MODEL_PATH}")
    return str(MODEL_PATH)


def _dist(a, b) -> float:
    return math.sqrt((a.x - b.x)**2 + (a.y - b.y)**2)


def _palm(lm):
    ids = [0, 5, 9, 13, 17]
    return (
        sum(lm[i].x for i in ids) / 5,
        sum(lm[i].y for i in ids) / 5,
    )


# ═════════════════════════════════════════════════════════════════════════════
#  GESTURE ENGINE v4 — Different-Finger Approach
# ═════════════════════════════════════════════════════════════════════════════
# ═════════════════════════════════════════════════════════════════════════════
#  TEST OVERLAY — Tap markers + Drag trails
# ═════════════════════════════════════════════════════════════════════════════
class TestOverlay:
    """Tracks tap markers and drag trails for visual testing."""

    def __init__(self):
        # Tap markers: list of (x_norm, y_norm, timestamp, gesture_type)
        self.tap_markers: list[tuple[float, float, float, str]] = []
        # Drag trail: list of (x_norm, y_norm) points during active drag
        self.drag_points: list[tuple[float, float]] = []
        # Finished drag trails: list of (points_list, end_timestamp)
        self.finished_trails: list[tuple[list[tuple[float, float]], float]] = []
        self.dragging = False

    def on_tap(self, x: float, y: float, gesture_type: str = "tap"):
        """Record a tap/double_tap at normalised (x, y)."""
        self.tap_markers.append((x, y, time.monotonic(), gesture_type))

    def on_drag_start(self, x: float, y: float):
        """Start a new drag trail."""
        self.drag_points = [(x, y)]
        self.dragging = True

    def on_drag_update(self, x: float, y: float):
        """Add a point to the current drag trail."""
        if self.dragging:
            self.drag_points.append((x, y))

    def on_drag_end(self):
        """Finish the current drag trail (it will persist for DRAG_TRAIL_LIFETIME)."""
        if self.drag_points:
            self.finished_trails.append((list(self.drag_points), time.monotonic()))
        self.drag_points = []
        self.dragging = False

    def draw(self, frame, fw: int, fh: int):
        """Draw all active markers and trails onto the frame."""
        now = time.monotonic()

        # ── Expired cleanup ──────────────────────────────────────────
        self.tap_markers = [
            m for m in self.tap_markers if now - m[2] < TAP_MARKER_LIFETIME
        ]
        self.finished_trails = [
            t for t in self.finished_trails if now - t[1] < DRAG_TRAIL_LIFETIME
        ]

        # ── Draw finished drag trails (fading) ───────────────────────
        for trail_pts, end_t in self.finished_trails:
            age = now - end_t
            alpha = max(0.0, 1.0 - age / DRAG_TRAIL_LIFETIME)
            self._draw_trail(frame, trail_pts, fw, fh, alpha)

        # ── Draw active drag trail ───────────────────────────────────
        if self.dragging and len(self.drag_points) > 1:
            self._draw_trail(frame, self.drag_points, fw, fh, 1.0)

        # ── Draw tap markers ─────────────────────────────────────────
        for mx, my, t, gtype in self.tap_markers:
            age = now - t
            alpha = max(0.0, 1.0 - age / TAP_MARKER_LIFETIME)
            px = int(mx * fw)
            py = int(my * fh)

            if gtype == "double_tap":
                col = (0, 165, 255)  # orange
                label = "DBL"
            else:
                col = (0, 255, 255)  # cyan/yellow
                label = "TAP"

            # Fade by adjusting colour intensity
            c = tuple(int(v * alpha) for v in col)

            # Crosshair
            r = MARKER_RADIUS
            cv2.line(frame, (px - r, py), (px + r, py), c, 2, cv2.LINE_AA)
            cv2.line(frame, (px, py - r), (px, py + r), c, 2, cv2.LINE_AA)
            cv2.circle(frame, (px, py), r, c, 1, cv2.LINE_AA)

            # Coordinate text
            coord_text = f"{label} ({mx:.3f}, {my:.3f})"
            cv2.putText(frame, coord_text, (px + r + 4, py - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, c, 1, cv2.LINE_AA)

    def _draw_trail(self, frame, pts, fw, fh, alpha):
        """Draw a polyline trail with thickness and glow."""
        if len(pts) < 2:
            return
        pixel_pts = [(int(x * fw), int(y * fh)) for x, y in pts]

        # Main line (green, fading)
        col = tuple(int(v * alpha) for v in (0, 255, 0))
        for i in range(len(pixel_pts) - 1):
            cv2.line(frame, pixel_pts[i], pixel_pts[i + 1], col, 3, cv2.LINE_AA)

        # Start dot (blue)
        sc = tuple(int(v * alpha) for v in (255, 200, 0))
        cv2.circle(frame, pixel_pts[0], 6, sc, -1, cv2.LINE_AA)

        # End dot (red)
        ec = tuple(int(v * alpha) for v in (0, 0, 255))
        cv2.circle(frame, pixel_pts[-1], 6, ec, -1, cv2.LINE_AA)

        # Distance label at end
        if len(pts) >= 2:
            total_dist = sum(
                math.sqrt((pts[i+1][0] - pts[i][0])**2 + (pts[i+1][1] - pts[i][1])**2)
                for i in range(len(pts) - 1)
            )
            dist_text = f"{total_dist:.3f}"
            ep = pixel_pts[-1]
            tc = tuple(int(v * alpha) for v in (0, 255, 0))
            cv2.putText(frame, dist_text, (ep[0] + 8, ep[1] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, tc, 1, cv2.LINE_AA)


class GestureEngine:
    """
    SIMPLE gesture mapping:

      Thumb + INDEX finger pinch:
        • Quick release (<300ms), no movement → TAP
        • Long hold (>500ms), no movement    → PINCH_HOLD
        • Movement while pinched             → PINCH_DRAG
        • Fast velocity at release           → PINCH_FLICK

      Thumb + MIDDLE finger pinch:
        • Any quick pinch-release (<300ms)   → DOUBLE_TAP

    No complex timing windows.  Each gesture is ONE distinct motion.
    """

    S_IDLE        = 0
    S_IDX_PINCH   = 1   # thumb+index pinching
    S_MID_PINCH   = 2   # thumb+middle pinching

    def __init__(self):
        self.state = self.S_IDLE

        # Which fingers are touching
        self.idx_pinched = False
        self.mid_pinched = False
        self.idx_dist    = 1.0
        self.mid_dist    = 1.0

        # Timing
        self.pinch_t0 = 0.0
        self.dur_ms   = 0.0

        # Movement
        self.prev_palm = None
        self.frame_dx  = 0.0
        self.frame_dy  = 0.0
        self.cum_move  = 0.0
        self.moved     = False

        # Velocity
        self._trail: deque[tuple[float, float, float]] = deque(maxlen=12)
        self.vel = 0.0

        # Output
        self.tap_count = 0
        self.gtype     = "none"
        self.gstate    = "idle"

        # Cursor — always tracks index finger tip (normalised 0-1)
        self.cursor_x  = 0.5
        self.cursor_y  = 0.5

        # Ghost prevention — cooldown after double_tap
        self._dbl_cooldown_until = 0.0

        # Flash
        self._flash_label = ""
        self._flash_until = 0.0

        # Test overlay
        self.overlay = TestOverlay()

    def update(self, lm: list) -> dict:
        now = time.monotonic()

        thumb  = lm[THUMB_TIP]
        index  = lm[INDEX_TIP]
        middle = lm[MIDDLE_TIP]

        # ── Cursor always follows index finger tip ───────────────────
        ix, iy = index.x, index.y
        self.cursor_x = ix
        self.cursor_y = iy

        # ── Raw distances ────────────────────────────────────────────
        self.idx_dist = _dist(thumb, index)
        self.mid_dist = _dist(thumb, middle)

        # ── Hysteresis for index pinch ───────────────────────────────
        if not self.idx_pinched and self.idx_dist < PINCH_DIST:
            self.idx_pinched = True
        elif self.idx_pinched and self.idx_dist > PINCH_EXIT:
            self.idx_pinched = False

        # ── Hysteresis for middle pinch ──────────────────────────────
        if not self.mid_pinched and self.mid_dist < PINCH_DIST:
            self.mid_pinched = True
        elif self.mid_pinched and self.mid_dist > PINCH_EXIT:
            self.mid_pinched = False

        # ── Velocity trail (index finger tip) ────────────────────────
        self._trail.append((ix, iy, now))

        # ── Reset per-frame ──────────────────────────────────────────
        self.frame_dx = 0.0
        self.frame_dy = 0.0

        # ═══ STATE MACHINE ═══════════════════════════════════════════

        if self.state == self.S_IDLE:
            self.gtype  = "none"
            self.gstate = "idle"

            if self.mid_pinched:
                # Middle finger pinch started → track for double_tap
                self._enter_mid_pinch(now)
            elif self.idx_pinched and now > self._dbl_cooldown_until:
                # Index finger pinch started (ghost-prevention check)
                self._enter_idx_pinch(now, ix, iy)

        elif self.state == self.S_MID_PINCH:
            self.dur_ms = (now - self.pinch_t0) * 1000

            if self.mid_pinched:
                # Still pinching middle → show "double_tap" preview
                self.gtype  = "double_tap"
                self.gstate = "start"
            else:
                # Released middle finger
                dur_s = self.dur_ms / 1000.0
                if dur_s < TAP_MAX_TIME:
                    self.gtype    = "double_tap"
                    self.gstate   = "end"
                    self.tap_count = 2
                    self._flash("DOUBLE TAP", now)
                    self._log("double_tap")
                    # Record tap marker at middle finger position
                    self.overlay.on_tap(middle.x, middle.y, "double_tap")
                    # Ghost prevention: ignore index for DOUBLE_TAP_COOL
                    self._dbl_cooldown_until = now + DOUBLE_TAP_COOL
                else:
                    # Held middle too long — ignore (not a tap)
                    self.gtype  = "none"
                    self.gstate = "idle"
                self.state = self.S_IDLE

        elif self.state == self.S_IDX_PINCH:
            self.dur_ms = (now - self.pinch_t0) * 1000

            if self.idx_pinched:
                # Still pinching index — track index finger movement
                if self.prev_palm is not None:
                    self.frame_dx = ix - self.prev_palm[0]
                    self.frame_dy = iy - self.prev_palm[1]
                    fm = math.sqrt(self.frame_dx**2 + self.frame_dy**2)
                    self.cum_move += fm
                self.prev_palm = (ix, iy)

                if self.cum_move > DRAG_DEADZONE:
                    self.moved = True

                if self.moved:
                    self.gtype  = "pinch_drag"
                    self.gstate = "active"
                    # Record drag trail point (index finger position)
                    self.overlay.on_drag_update(ix, iy)
                elif self.dur_ms > HOLD_MIN_MS:
                    self.gtype  = "pinch_hold"
                    self.gstate = "active"
                else:
                    self.gtype  = "pinch_hold"
                    self.gstate = "start"

            else:
                # ── INDEX RELEASED ───────────────────────────────────
                self.vel = self._calc_vel()
                dur_s = self.dur_ms / 1000.0

                if self.moved and self.vel > FLICK_VELOCITY:
                    self.gtype  = "pinch_flick"
                    self.gstate = "end"
                    self._flash("FLICK", now)
                    self._log("pinch_flick")
                    self.overlay.on_drag_end()

                elif dur_s < TAP_MAX_TIME and not self.moved:
                    self.gtype    = "tap"
                    self.gstate   = "end"
                    self.tap_count = 1
                    self._flash("TAP", now)
                    self._log("tap")
                    # Record tap marker at index finger position
                    self.overlay.on_tap(index.x, index.y, "tap")

                elif self.moved:
                    self.gtype  = "pinch_drag"
                    self.gstate = "end"
                    self._log("pinch_drag end")
                    self.overlay.on_drag_end()

                else:
                    self.gtype  = "pinch_hold"
                    self.gstate = "end"
                    self._log("pinch_hold end")

                self._reset_pinch()
                self.state = self.S_IDLE

        return self._payload(lm)

    # ── Transitions ──────────────────────────────────────────────────────
    def _enter_idx_pinch(self, now, ix, iy):
        self.state     = self.S_IDX_PINCH
        self.pinch_t0  = now
        self.prev_palm = (ix, iy)
        self.cum_move  = 0.0
        self.moved     = False
        self._trail.clear()
        self._trail.append((ix, iy, now))
        self.overlay.on_drag_start(ix, iy)  # prepare drag start point
        self.gtype  = "pinch_hold"
        self.gstate = "start"

    def _enter_mid_pinch(self, now):
        self.state    = self.S_MID_PINCH
        self.pinch_t0 = now
        self.gtype  = "double_tap"
        self.gstate = "start"

    def _reset_pinch(self):
        self.prev_palm = None
        self.cum_move  = 0.0
        self.moved     = False

    def _calc_vel(self) -> float:
        """Distance travelled in the last ~0.1s window."""
        if len(self._trail) < 3:
            return 0.0
        now = self._trail[-1][2]
        # Find the sample closest to 0.1s ago
        target_t = now - 0.1
        best = self._trail[0]
        for pt in self._trail:
            if pt[2] <= target_t:
                best = pt
            else:
                break
        x0, y0, t0 = best
        x1, y1, t1 = self._trail[-1]
        dt = t1 - t0
        if dt < 0.01:
            return 0.0
        # Return distance over 0.1s (not per-second)
        return math.sqrt((x1 - x0)**2 + (y1 - y0)**2) * (0.1 / dt)

    def _flash(self, label: str, now: float):
        self._flash_label = label
        self._flash_until = now + 0.6

    def _log(self, tag: str):
        print(f"  ▸ GESTURE: {tag:<20}  idx={self.idx_dist:.4f}  "
              f"mid={self.mid_dist:.4f}  dur={self.dur_ms:.0f}ms  "
              f"vel={self.vel:.3f}  move={self.cum_move:.4f}")

    def _payload(self, lm) -> dict:
        return {
            "event_id":  f"evt_{uuid.uuid4().hex[:12]}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "success":   True,
            "gesture": {
                "type":  self.gtype,
                "state": self.gstate,
                "confidence": 1.0 - min(self.idx_dist, self.mid_dist),
                "handedness": "Right",
                "target_element_id": None,
                "tracking_data": {
                    "landmarks": [
                        {"id": i, "x": round(l.x, 5), "y": round(l.y, 5),
                         "z": round(l.z, 5)} for i, l in enumerate(lm)
                    ],
                    "world_coordinates": {
                        "x": round(lm[INDEX_TIP].x, 5),
                        "y": round(lm[INDEX_TIP].y, 5),
                        "z": round(lm[INDEX_TIP].z, 5),
                    },
                },
                "interaction_data": {
                    "pinch_status": {
                        "is_pinched":     self.idx_pinched or self.mid_pinched,
                        "pinch_strength": round(max(0.0, 1.0 - self.idx_dist / PINCH_EXIT), 4),
                        "pinch_distance": round(self.idx_dist, 5),
                    },
                    "movement": {
                        "origin":  {"x": round(self.cursor_x, 5),
                                    "y": round(self.cursor_y, 5)},
                        "current": {"x": round(self.cursor_x, 5),
                                    "y": round(self.cursor_y, 5)},
                        "delta": {
                            "dx": round(self.frame_dx, 5),
                            "dy": round(self.frame_dy, 5),
                        },
                        "velocity": {
                            "vx": round(self.frame_dx * 1000, 2),
                            "vy": round(self.frame_dy * 1000, 2),
                        },
                    },
                    "timing": {
                        "duration_ms": round(self.dur_ms, 1),
                        "tap_count":   self.tap_count,
                    },
                },
            },
            "cursor": {
                "x": round(self.cursor_x, 5),
                "y": round(self.cursor_y, 5),
            },
        }

    # ── HUD properties ───────────────────────────────────────────────────
    @property
    def flash_label(self) -> str:
        if time.monotonic() < self._flash_until:
            return self._flash_label
        return ""

    @property
    def pinched(self) -> bool:
        return self.idx_pinched or self.mid_pinched

    @property
    def label(self) -> str:
        return f"{self.gtype} ({self.gstate})"


# ═════════════════════════════════════════════════════════════════════════════
#  DRAWING
# ═════════════════════════════════════════════════════════════════════════════

def draw_hand(frame, lm, w, h, eng: GestureEngine):
    pts = [(int(l.x * w), int(l.y * h)) for l in lm]

    # Skeleton
    sc = (0, 255, 120) if eng.pinched else (50, 180, 50)
    for s, e in HAND_CONNS:
        cv2.line(frame, pts[s], pts[e], sc, 2, cv2.LINE_AA)

    # Landmarks
    highlight = {THUMB_TIP, INDEX_TIP, MIDDLE_TIP}
    for i, p in enumerate(pts):
        if i in highlight:
            if i == THUMB_TIP:
                col = (0, 200, 255)
            elif i == INDEX_TIP:
                col = (0, 255, 255) if eng.idx_pinched else (255, 200, 100)
            else:  # MIDDLE_TIP
                col = (0, 165, 255) if eng.mid_pinched else (255, 100, 200)
            cv2.circle(frame, p, 9, col, -1, cv2.LINE_AA)
            cv2.circle(frame, p, 9, (0, 0, 0), 1, cv2.LINE_AA)
        else:
            cv2.circle(frame, p, 4, (255, 255, 255), -1, cv2.LINE_AA)
            cv2.circle(frame, p, 4, (0, 0, 0), 1, cv2.LINE_AA)

    # Pinch lines: thumb-to-index and thumb-to-middle
    idx_col = (0, 0, 255) if eng.idx_pinched else (80, 80, 80)
    mid_col = (0, 165, 255) if eng.mid_pinched else (60, 60, 60)
    cv2.line(frame, pts[THUMB_TIP], pts[INDEX_TIP], idx_col,
             3 if eng.idx_pinched else 1, cv2.LINE_AA)
    cv2.line(frame, pts[THUMB_TIP], pts[MIDDLE_TIP], mid_col,
             3 if eng.mid_pinched else 1, cv2.LINE_AA)

    # Labels near fingertips
    cv2.putText(frame, "TAP", (pts[INDEX_TIP][0]+12, pts[INDEX_TIP][1]-5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(frame, "DBL", (pts[MIDDLE_TIP][0]+12, pts[MIDDLE_TIP][1]-5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 165, 255), 1, cv2.LINE_AA)


def draw_hud(frame, eng: GestureEngine, fps: float):
    h, w = frame.shape[:2]
    gt  = eng.gtype
    col = GCOLORS.get(gt, (160, 160, 160))

    # ── Top bar ──────────────────────────────────────────────────────
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 70), (10, 10, 10), -1)
    cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)

    cv2.putText(frame, gt.upper(), (14, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, col, 2, cv2.LINE_AA)
    cv2.putText(frame, eng.gstate, (14, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (140, 140, 140), 1, cv2.LINE_AA)
    cv2.putText(frame, f"{fps:.0f} FPS", (w - 110, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (80, 180, 255), 2, cv2.LINE_AA)

    # Legend (top-right)
    cv2.putText(frame, "Index = Tap/Hold/Drag", (w - 230, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(frame, "Middle = Double Tap", (w - 230, 65),
                cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 165, 255), 1, cv2.LINE_AA)

    # ── Bottom bar ───────────────────────────────────────────────────
    overlay2 = frame.copy()
    cv2.rectangle(overlay2, (0, h - 36), (w, h), (10, 10, 10), -1)
    cv2.addWeighted(overlay2, 0.75, frame, 0.25, 0, frame)

    info = (f"idx:{eng.idx_dist:.3f}  mid:{eng.mid_dist:.3f}  "
            f"dur:{eng.dur_ms:.0f}ms  vel:{eng.vel:.2f}")
    cv2.putText(frame, info, (10, h - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (160, 160, 160), 1, cv2.LINE_AA)

    status = "INDEX" if eng.idx_pinched else ("MIDDLE" if eng.mid_pinched else "open")
    scol   = (0, 255, 120) if eng.pinched else (80, 80, 80)
    cv2.putText(frame, status, (w - 90, h - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, scol, 1, cv2.LINE_AA)

    # ── Flash banner (600ms) ─────────────────────────────────────────
    flash = eng.flash_label
    if flash:
        sz = cv2.getTextSize(flash, cv2.FONT_HERSHEY_SIMPLEX, 2.0, 4)[0]
        tx = (w - sz[0]) // 2
        ty = (h + sz[1]) // 2
        cv2.putText(frame, flash, (tx + 2, ty + 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 0, 0), 6, cv2.LINE_AA)
        cv2.putText(frame, flash, (tx, ty),
                    cv2.FONT_HERSHEY_SIMPLEX, 2.0, col, 4, cv2.LINE_AA)


def draw_cursor(frame, eng: GestureEngine):
    """Draw a persistent cursor pointer at the index finger tip."""
    fh, fw = frame.shape[:2]
    cx = int(eng.cursor_x * fw)
    cy = int(eng.cursor_y * fh)

    # Outer glow ring
    if eng.idx_pinched:
        ring_col = (0, 0, 255)      # red when pinched
        ring_r   = 18
    else:
        ring_col = (0, 255, 255)    # cyan when open
        ring_r   = 14

    cv2.circle(frame, (cx, cy), ring_r, ring_col, 2, cv2.LINE_AA)

    # Crosshair lines extending beyond ring
    ext = ring_r + 8
    cv2.line(frame, (cx - ext, cy), (cx - ring_r - 2, cy), ring_col, 1, cv2.LINE_AA)
    cv2.line(frame, (cx + ring_r + 2, cy), (cx + ext, cy), ring_col, 1, cv2.LINE_AA)
    cv2.line(frame, (cx, cy - ext), (cx, cy - ring_r - 2), ring_col, 1, cv2.LINE_AA)
    cv2.line(frame, (cx, cy + ring_r + 2), (cx, cy + ext), ring_col, 1, cv2.LINE_AA)

    # Center dot
    cv2.circle(frame, (cx, cy), 3, (255, 255, 255), -1, cv2.LINE_AA)

    # Coordinate readout (top-right of cursor)
    coord = f"({eng.cursor_x:.3f}, {eng.cursor_y:.3f})"
    cv2.putText(frame, coord, (cx + ext + 4, cy - 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, ring_col, 1, cv2.LINE_AA)

    # Also draw test overlay (tap markers + drag trails)
    eng.overlay.draw(frame, fw, fh)


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN LOOP
# ═════════════════════════════════════════════════════════════════════════════

async def run():
    model_path = ensure_model()

    cap = cv2.VideoCapture(CAMERA_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  CAM_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_HEIGHT)
    if not cap.isOpened():
        print(f"[ERR] Cannot open camera {CAMERA_INDEX}")
        sys.exit(1)
    print(f"[INFO] Camera {int(cap.get(3))}×{int(cap.get(4))}")

    eng = GestureEngine()
    fps = 0.0
    pt  = time.monotonic()

    opts = HandLandmarkerOpts(
        base_options=BaseOptions(model_asset_path=model_path),
        running_mode=VisionRunningMode.VIDEO,
        num_hands=1,
        min_hand_detection_confidence=0.5,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.4,
    )
    det = HandLandmarker.create_from_options(opts)

    ws = None
    drain = None

    async def _drain(s):
        try:
            async for _ in s:
                pass
        except Exception:
            pass

    async def connect():
        nonlocal ws, drain
        if drain and not drain.done():
            drain.cancel()
        while ws is None:
            try:
                ws = await websockets.connect(
                    WS_URL, max_size=2**20,
                    ping_interval=20, ping_timeout=10, close_timeout=5,
                )
                drain = asyncio.create_task(_drain(ws))
                print(f"[INFO] WS → {WS_URL}")
            except Exception as e:
                print(f"[WARN] WS fail ({e}), retry {RECONNECT_DELAY}s")
                await asyncio.sleep(RECONNECT_DELAY)

    await connect()
    ts_ms = 0

    try:
        while True:
            t0 = time.monotonic()

            ok, frame = cap.read()
            if not ok:
                await asyncio.sleep(0.01)
                continue

            frame = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            ts_ms += int(FRAME_INTERVAL * 1000)
            res = det.detect_for_video(img, ts_ms)

            fh, fw = frame.shape[:2]

            if res.hand_landmarks:
                lm = res.hand_landmarks[0]
                draw_hand(frame, lm, fw, fh, eng)
                payload = eng.update(list(lm))
                raw = orjson.dumps(payload)
                try:
                    await ws.send(raw)
                except Exception:
                    print("[WARN] WS send fail, reconnecting …")
                    try: await ws.close()
                    except: pass
                    ws = None
                    await connect()

            now = time.monotonic()
            fps = 1.0 / (now - pt) if (now - pt) > 0 else 0
            pt  = now

            draw_cursor(frame, eng)
            draw_hud(frame, eng, fps)
            cv2.imshow("Gesture Controller", frame)
            if cv2.waitKey(1) & 0xFF == 27:
                break

            sl = FRAME_INTERVAL - (time.monotonic() - t0)
            if sl > 0:
                await asyncio.sleep(sl)

    finally:
        det.close()
        cap.release()
        cv2.destroyAllWindows()
        if drain and not drain.done():
            drain.cancel()
        if ws:
            try: await ws.close()
            except: pass
        print("[INFO] Done.")


if __name__ == "__main__":
    print("╔════════════════════════════════════════════════════════════╗")
    print("║  Gesture Controller v4                                    ║")
    print("║                                                           ║")
    print("║  Thumb + INDEX finger  → tap / hold / drag / flick       ║")
    print("║  Thumb + MIDDLE finger → double tap                      ║")
    print("║                                                           ║")
    print("║  Press ESC to quit                                        ║")
    print("╚════════════════════════════════════════════════════════════╝")
    asyncio.run(run())
