"""
Layer 4: Vision Fallback — Screenshot analysis via Gemini for visual UI understanding.
This is the intelligent fallback when the accessibility tree can't identify elements.
"""

import json
import requests
from tools.base import BaseTool, ToolDefinition, ToolResult, ToolResultStatus, LayerType
from utils.screenshot import capture_screenshot, screenshot_to_base64, scale_coordinates
from config import GEMINI_API_BASE, GEMINI_API_KEY, GEMINI_MODEL, VISION_CONFIDENCE_THRESHOLD


class VisionLayer(BaseTool):
    """
    Vision-based UI understanding using Gemini's multimodal capabilities.
    Takes screenshots and asks the LLM to identify elements and coordinates.
    """

    def __init__(self):
        super().__init__()
        self._last_scale = 1.0

        self._definitions = [
            ToolDefinition(
                name="screenshot_analyze",
                description=(
                    "Take a screenshot of the desktop and analyze it with AI vision. "
                    "Describe what you see, identify UI elements, or answer questions "
                    "about the current screen state."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "What to analyze in the screenshot, e.g. 'What application is open?'"
                        }
                    },
                    "required": ["question"]
                },
                layer=LayerType.VISION,
            ),
            ToolDefinition(
                name="find_element_visual",
                description=(
                    "Find a UI element on screen by visual description. Returns the "
                    "coordinates [x, y] where the element is located. "
                    "Use when the accessibility tree doesn't expose the element."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "element_description": {
                            "type": "string",
                            "description": "Visual description of the element to find, e.g. 'the blue Submit button'"
                        }
                    },
                    "required": ["element_description"]
                },
                layer=LayerType.VISION,
            ),
            ToolDefinition(
                name="verify_action",
                description=(
                    "Take a screenshot and verify that a previous action succeeded. "
                    "Compare the current screen state against expected outcome."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "What the screen should look like after the action, e.g. 'Notepad should be open with text Hello'"
                        }
                    },
                    "required": ["description"]
                },
                layer=LayerType.VISION,
            ),
            ToolDefinition(
                name="read_screen_text",
                description=(
                    "Read/extract text from a specific region of the screen using AI vision (OCR). "
                    "Specify a rectangular region, or leave empty for full screen. "
                    "Returns all text visible in that region."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer", "description": "Left X of region (default: 0)", "default": 0},
                        "y": {"type": "integer", "description": "Top Y of region (default: 0)", "default": 0},
                        "width": {"type": "integer", "description": "Width of region (default: full screen)", "default": 0},
                        "height": {"type": "integer", "description": "Height of region (default: full screen)", "default": 0}
                    }
                },
                layer=LayerType.VISION,
            ),
            ToolDefinition(
                name="wait_for_element",
                description=(
                    "Wait until a specific visual element appears on screen. "
                    "Polls the screen every interval seconds up to the timeout. "
                    "Use to wait for loading screens, dialogs, or app launches to complete."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "element_description": {
                            "type": "string",
                            "description": "What to look for, e.g. 'a Save dialog', 'the Chrome browser window'"
                        },
                        "timeout": {"type": "integer", "description": "Max seconds to wait (default: 10)", "default": 10},
                        "interval": {"type": "number", "description": "Seconds between checks (default: 2)", "default": 2}
                    },
                    "required": ["element_description"]
                },
                layer=LayerType.VISION,
            ),
        ]

    def _call_gemini_vision(self, prompt: str, image_base64: str) -> str:
        """Send an image + prompt to Gemini via the local Antigravity proxy."""
        url = f"{GEMINI_API_BASE}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {GEMINI_API_KEY}",
        }
        payload = {
            "model": GEMINI_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_base64}"
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        }
                    ]
                }
            ],
            "max_tokens": 1024,
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                f"Cannot reach Gemini API at {GEMINI_API_BASE}. "
                f"Is the Antigravity proxy running?"
            )
        except Exception as e:
            raise RuntimeError(f"Gemini vision call failed: {str(e)}")

    def _execute_screenshot_analyze(self, question: str = None, **kwargs) -> ToolResult:
        """Take a screenshot and analyze it."""
        # Accept common LLM parameter aliases
        question = question or kwargs.get("description") or kwargs.get("prompt") or "Describe what you see on the screen."
        try:
            img, scale = capture_screenshot()
            self._last_scale = scale
            img_b64 = screenshot_to_base64(img)

            prompt = (
                "You are analyzing a Windows desktop screenshot.\n"
                f"Question: {question}\n\n"
                "Provide a clear, detailed answer based on what you see in the screenshot."
            )

            analysis = self._call_gemini_vision(prompt, img_b64)

            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output=analysis,
                screenshot_base64=img_b64,
                layer_used="vision",
            )

        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Screenshot analysis failed: {str(e)}",
                layer_used="vision",
            )

    def _execute_find_element_visual(self, element_description: str = None, **kwargs) -> ToolResult:
        """Find a UI element by visual description and return its coordinates."""
        # Accept common LLM parameter aliases
        element_description = element_description or kwargs.get("description") or kwargs.get("element") or "unknown element"
        try:
            img, scale = capture_screenshot()
            self._last_scale = scale
            img_b64 = screenshot_to_base64(img)

            prompt = (
                "You are looking at a Windows desktop screenshot.\n"
                f"Find this UI element: {element_description}\n\n"
                "You MUST respond with ONLY valid JSON in this exact format:\n"
                "{\n"
                '  "found": true/false,\n'
                '  "x": <x pixel coordinate of the center of the element>,\n'
                '  "y": <y pixel coordinate of the center of the element>,\n'
                '  "description": "<what you see at that location>",\n'
                '  "confidence": <0-100 how confident you are>\n'
                "}\n\n"
                "The coordinates should be pixel positions in the screenshot. "
                "Be as precise as possible."
            )

            response_text = self._call_gemini_vision(prompt, img_b64)

            # Parse JSON from response (handle markdown code blocks)
            json_str = response_text
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0]
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0]

            result = json.loads(json_str.strip())

            if not result.get("found", False):
                return ToolResult(
                    status=ToolResultStatus.ERROR,
                    output=f"Element not found: {element_description}",
                    error=result.get("description", "Element not visible on screen"),
                    layer_used="vision",
                    confidence=result.get("confidence", 0),
                )

            # Scale coordinates back to actual screen resolution
            x, y = scale_coordinates(result["x"], result["y"], scale)
            confidence = result.get("confidence", 50)

            if confidence < VISION_CONFIDENCE_THRESHOLD:
                return ToolResult(
                    status=ToolResultStatus.NEEDS_VISION_FALLBACK,
                    output=f"Low confidence ({confidence}%) for element: {element_description}",
                    data={"x": x, "y": y, "confidence": confidence, "description": result.get("description", "")},
                    layer_used="vision",
                    confidence=confidence,
                )

            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output=f"Found '{element_description}' at ({x}, {y}) - {result.get('description', '')}",
                data={"x": x, "y": y, "confidence": confidence, "description": result.get("description", "")},
                layer_used="vision",
                confidence=confidence,
            )

        except json.JSONDecodeError:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Vision model returned invalid JSON. Raw response: {response_text[:300]}",
                layer_used="vision",
            )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Find element failed: {str(e)}",
                layer_used="vision",
            )

    def _execute_verify_action(self, description: str = None, **kwargs) -> ToolResult:
        """Verify that a previous action succeeded by taking a screenshot."""
        # Accept common LLM parameter aliases
        description = description or kwargs.get("expected_outcome") or kwargs.get("action") or "Check if the action succeeded"
        try:
            img, scale = capture_screenshot()
            img_b64 = screenshot_to_base64(img)

            prompt = (
                "You are verifying that an action was performed correctly on a Windows desktop.\n"
                f"Expected outcome: {description}\n\n"
                "Look at the screenshot and respond with ONLY valid JSON:\n"
                "{\n"
                '  "success": true/false,\n'
                '  "description": "<what you actually see>",\n'
                '  "confidence": <0-100>\n'
                "}"
            )

            response_text = self._call_gemini_vision(prompt, img_b64)

            # Parse JSON from response
            json_str = response_text
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0]
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0]

            result = json.loads(json_str.strip())

            status = ToolResultStatus.SUCCESS if result.get("success", False) else ToolResultStatus.ERROR
            return ToolResult(
                status=status,
                output=result.get("description", ""),
                data=result,
                layer_used="vision",
                confidence=result.get("confidence", 50),
            )

        except json.JSONDecodeError:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Vision verification returned invalid JSON: {response_text[:300]}",
                layer_used="vision",
            )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Verify action failed: {str(e)}",
                layer_used="vision",
            )

    def _execute_read_screen_text(self, x: int = 0, y: int = 0,
                                   width: int = 0, height: int = 0) -> ToolResult:
        """Read/extract all text from a screen region using vision OCR."""
        import time as _time

        try:
            # Capture region or full screen
            region = None
            if width > 0 and height > 0:
                region = (x, y, width, height)

            img, scale = capture_screenshot(region=region)
            img_b64 = screenshot_to_base64(img)

            prompt = (
                "You are an OCR system. Read ALL text visible in this screenshot.\n"
                "Output the text exactly as it appears on screen, preserving layout.\n"
                "Include: menu items, button labels, window titles, body text, "
                "status bar text, notifications, URLs, file paths — everything.\n"
                "Output ONLY the text, no commentary."
            )

            text = self._call_gemini_vision(prompt, img_b64)

            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output=text.strip(),
                data={"region": {"x": x, "y": y, "width": width, "height": height}},
                layer_used="vision",
            )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Screen text reading failed: {str(e)}",
                layer_used="vision",
            )

    def _execute_wait_for_element(self, element_description: str,
                                   timeout: int = 10, interval: float = 2.0) -> ToolResult:
        """Wait until a visual element appears on screen."""
        import time as _time

        start = _time.time()
        attempts = 0

        while _time.time() - start < timeout:
            attempts += 1
            try:
                img, scale = capture_screenshot()
                self._last_scale = scale
                img_b64 = screenshot_to_base64(img)

                prompt = (
                    f"Looking at this Windows screenshot, is the following visible?\n"
                    f"Element: {element_description}\n\n"
                    "Respond with ONLY valid JSON:\n"
                    "{\n"
                    '  "found": true/false,\n'
                    '  "description": "<what you see>"\n'
                    "}"
                )

                response_text = self._call_gemini_vision(prompt, img_b64)

                json_str = response_text
                if "```json" in json_str:
                    json_str = json_str.split("```json")[1].split("```")[0]
                elif "```" in json_str:
                    json_str = json_str.split("```")[1].split("```")[0]

                result = json.loads(json_str.strip())

                if result.get("found", False):
                    elapsed = round(_time.time() - start, 1)
                    return ToolResult(
                        status=ToolResultStatus.SUCCESS,
                        output=f"Element found after {elapsed}s ({attempts} attempts): {result.get('description', element_description)}",
                        data={"found": True, "elapsed": elapsed, "attempts": attempts},
                        layer_used="vision",
                    )

            except Exception:
                pass  # Continue polling

            if _time.time() - start + interval < timeout:
                _time.sleep(interval)
            else:
                break

        elapsed = round(_time.time() - start, 1)
        return ToolResult(
            status=ToolResultStatus.ERROR,
            output="",
            error=f"Element '{element_description}' not found after {elapsed}s ({attempts} attempts)",
            data={"found": False, "elapsed": elapsed, "attempts": attempts},
            layer_used="vision",
        )

