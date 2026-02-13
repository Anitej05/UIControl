"""
Utility: Fast screenshot capture using mss + PIL.
Captures the screen and optionally resizes for sending to vision models.
"""

import base64
import io
from PIL import Image

try:
    import mss
    HAS_MSS = True
except ImportError:
    HAS_MSS = False

from config import SCREENSHOT_MAX_WIDTH, SCREENSHOT_MAX_HEIGHT


def capture_screenshot(
    monitor: int = 0,
    max_width: int = SCREENSHOT_MAX_WIDTH,
    max_height: int = SCREENSHOT_MAX_HEIGHT,
    region: tuple | None = None,
) -> tuple[Image.Image, float]:
    """
    Capture the screen and return a PIL Image + scale factor.

    Args:
        monitor: Monitor index (0 = primary, -1 = all)
        max_width: Max width for downscaling
        max_height: Max height for downscaling
        region: Optional (left, top, width, height) to capture a specific region

    Returns:
        (PIL Image, scale_factor) where scale_factor maps LLM coords back to real screen
    """
    if HAS_MSS:
        with mss.mss() as sct:
            if region:
                monitor_def = {
                    "left": region[0], "top": region[1],
                    "width": region[2], "height": region[3]
                }
            else:
                monitors = sct.monitors
                # monitors[0] is the combined, monitors[1] is primary
                idx = 1 if monitor == 0 else (monitor + 1 if monitor > 0 else 0)
                monitor_def = monitors[min(idx, len(monitors) - 1)]

            raw = sct.grab(monitor_def)
            img = Image.frombytes("RGB", raw.size, raw.rgb)
    else:
        # Fallback to PIL.ImageGrab
        from PIL import ImageGrab
        if region:
            left, top, w, h = region
            img = ImageGrab.grab(bbox=(left, top, left + w, top + h))
        else:
            img = ImageGrab.grab()

    # Calculate scale factor
    orig_w, orig_h = img.size
    scale_w = max_width / orig_w if orig_w > max_width else 1.0
    scale_h = max_height / orig_h if orig_h > max_height else 1.0
    scale = min(scale_w, scale_h)

    if scale < 1.0:
        new_w = int(orig_w * scale)
        new_h = int(orig_h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    return img, scale


def screenshot_to_base64(img: Image.Image, format: str = "PNG") -> str:
    """Convert a PIL Image to a base64-encoded string."""
    buffer = io.BytesIO()
    img.save(buffer, format=format)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def scale_coordinates(x: int, y: int, scale: float) -> tuple[int, int]:
    """
    Scale coordinates from the LLM's downscaled image back to actual screen coords.
    The LLM sees a potentially downscaled image, so we reverse the scale.
    """
    if scale <= 0 or scale >= 1.0:
        return x, y
    return int(x / scale), int(y / scale)
