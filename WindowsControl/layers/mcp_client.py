"""
Layer 2: UI Automation — Native accessibility tree + PyAutoGUI interaction.
Reimplements the core Windows-MCP functionality directly using comtypes UIAutomation.
No external subprocess or Python 3.13 dependency needed.
"""

import time
import json
import ctypes
from typing import Optional
from tools.base import BaseTool, ToolDefinition, ToolResult, ToolResultStatus, LayerType
from utils.screenshot import capture_screenshot, screenshot_to_base64

try:
    import pyautogui
    pyautogui.FAILSAFE = False  # Disable fail-safe corner
    pyautogui.PAUSE = 0.1
    HAS_PYAUTOGUI = True
except ImportError:
    HAS_PYAUTOGUI = False

try:
    import comtypes
    import comtypes.client
    HAS_COMTYPES = True
except ImportError:
    HAS_COMTYPES = False


# UIAutomation COM interface IDs
IID_IUIAutomation = comtypes.GUID("{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}")
CLSID_CUIAutomation = comtypes.GUID("{FF48DBA4-60EF-4201-AA87-54103EEF594E}")


class UIAutomationHelper:
    """
    Wrapper around Windows UI Automation COM API to extract the accessibility tree.
    This is the core tech behind Windows-MCP's Snapshot tool.
    """

    def __init__(self):
        self._automation = None
        self._initialize()

    def _initialize(self):
        """Initialize the UIAutomation COM object."""
        try:
            self._automation = comtypes.CoCreateInstance(
                CLSID_CUIAutomation, interface=comtypes.gen.UIAutomationClient.IUIAutomation
            )
        except Exception:
            # Fallback: try generating the type library first
            try:
                comtypes.client.GetModule("UIAutomationCore.dll")
                from comtypes.gen.UIAutomationClient import IUIAutomation
                self._automation = comtypes.CoCreateInstance(
                    CLSID_CUIAutomation, interface=IUIAutomation
                )
            except Exception as e:
                raise RuntimeError(f"Failed to initialize UIAutomation: {e}")

    def get_focused_window(self) -> dict:
        """Get information about the currently focused window."""
        try:
            focused = self._automation.GetFocusedElement()
            return {
                "name": focused.CurrentName or "",
                "control_type": focused.CurrentControlType,
                "class_name": focused.CurrentClassName or "",
            }
        except Exception:
            return {"name": "Unknown", "control_type": 0, "class_name": ""}

    def get_interactive_elements(self, max_depth: int = 10, max_elements: int = 500) -> list[dict]:
        """
        Walk the UI tree and collect interactive elements (buttons, links, text fields, etc.)
        Returns a list of elements with their names, types, and bounding rectangles.
        """
        elements = []
        try:
            root = self._automation.GetRootElement()
            self._walk_tree(root, elements, 0, max_depth, max_elements)
        except Exception as e:
            pass
        return elements

    def _walk_tree(self, element, elements: list, depth: int, max_depth: int, max_elements: int):
        """Recursively walk the UI tree."""
        if depth > max_depth or len(elements) >= max_elements:
            return

        try:
            # Interactive control types we care about
            INTERACTIVE_TYPES = {
                50000,  # Button
                50002,  # CheckBox
                50003,  # ComboBox
                50004,  # Edit
                50005,  # Hyperlink
                50007,  # List
                50009,  # MenuItem
                50020,  # TabItem
                50025,  # TreeItem
                50030,  # RadioButton
            }

            control_type = element.CurrentControlType
            name = element.CurrentName or ""
            is_interactive = control_type in INTERACTIVE_TYPES

            if is_interactive and name:
                try:
                    rect = element.CurrentBoundingRectangle
                    if rect.right > rect.left and rect.bottom > rect.top:
                        cx = (rect.left + rect.right) // 2
                        cy = (rect.top + rect.bottom) // 2
                        elements.append({
                            "name": name,
                            "type": self._get_control_type_name(control_type),
                            "x": cx,
                            "y": cy,
                            "rect": {
                                "left": rect.left, "top": rect.top,
                                "right": rect.right, "bottom": rect.bottom
                            }
                        })
                except Exception:
                    pass

            # Walk children
            try:
                walker = self._automation.ControlViewWalker
                child = walker.GetFirstChildElement(element)
                while child is not None and len(elements) < max_elements:
                    self._walk_tree(child, elements, depth + 1, max_depth, max_elements)
                    child = walker.GetNextSiblingElement(child)
            except Exception:
                pass

        except Exception:
            pass

    @staticmethod
    def _get_control_type_name(type_id: int) -> str:
        """Map control type ID to human-readable name."""
        names = {
            50000: "Button", 50001: "Calendar", 50002: "CheckBox",
            50003: "ComboBox", 50004: "Edit", 50005: "Hyperlink",
            50006: "Image", 50007: "List", 50008: "ListItem",
            50009: "MenuItem", 50010: "MenuBar", 50011: "ProgressBar",
            50012: "RadioButton", 50013: "ScrollBar", 50014: "Slider",
            50015: "Spinner", 50016: "StatusBar", 50017: "Tab",
            50020: "TabItem", 50021: "Text", 50025: "TreeItem",
            50033: "Pane", 50032: "Window",
        }
        return names.get(type_id, f"Control({type_id})")


class UIAutomationLayer(BaseTool):
    """
    Native UI automation layer using comtypes UIAutomation + PyAutoGUI.
    Provides all the same tools as Windows-MCP without the Python 3.13 dependency.
    """

    def __init__(self):
        super().__init__()
        if not HAS_PYAUTOGUI:
            raise RuntimeError("pyautogui not installed. Run: pip install pyautogui")

        self._ui_helper: Optional[UIAutomationHelper] = None

        self._definitions = [
            ToolDefinition(
                name="snapshot",
                description=(
                    "Capture the current desktop state: focused window, open windows, "
                    "and interactive elements (buttons, text fields, links) with their "
                    "screen coordinates. Set use_vision=true to also include a screenshot. "
                    "ALWAYS call this first to understand what's on screen before clicking."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "use_vision": {
                            "type": "boolean",
                            "description": "Also capture a screenshot for visual analysis.",
                            "default": False
                        }
                    }
                },
                layer=LayerType.MCP,
            ),
            ToolDefinition(
                name="click",
                description=(
                    "Click on the screen at coordinates [x, y]. "
                    "button: 'left' (default), 'right' (context menu), 'middle'. "
                    "clicks: 1=single (default), 2=double-click."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer", "description": "X coordinate to click"},
                        "y": {"type": "integer", "description": "Y coordinate to click"},
                        "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                        "clicks": {"type": "integer", "enum": [1, 2], "default": 1}
                    },
                    "required": ["x", "y"]
                },
                layer=LayerType.MCP,
            ),
            ToolDefinition(
                name="type_text",
                description=(
                    "Type text at coordinates [x, y]. Clicks the location first, then types. "
                    "Set clear=true to select-all and delete existing text first. "
                    "Set press_enter=true to press Enter after typing."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer", "description": "X coordinate of text field"},
                        "y": {"type": "integer", "description": "Y coordinate of text field"},
                        "text": {"type": "string", "description": "Text to type"},
                        "clear": {"type": "boolean", "default": False},
                        "press_enter": {"type": "boolean", "default": False}
                    },
                    "required": ["x", "y", "text"]
                },
                layer=LayerType.MCP,
            ),
            ToolDefinition(
                name="scroll",
                description=(
                    "Scroll at coordinates [x, y]. Positive amount scrolls up, negative scrolls down. "
                    "Default scrolls down 3 clicks."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer", "description": "X coordinate"},
                        "y": {"type": "integer", "description": "Y coordinate"},
                        "direction": {"type": "string", "enum": ["up", "down", "left", "right"], "default": "down"},
                        "amount": {"type": "integer", "description": "Scroll clicks (default 3)", "default": 3}
                    },
                    "required": ["x", "y"]
                },
                layer=LayerType.MCP,
            ),
            ToolDefinition(
                name="keyboard_shortcut",
                description=(
                    "Press keyboard shortcuts like 'ctrl+c', 'ctrl+v', 'alt+tab', "
                    "'win+r', 'ctrl+shift+esc', 'enter', 'escape', 'tab'."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "shortcut": {"type": "string", "description": "Key combination, e.g. 'ctrl+c'"}
                    },
                    "required": ["shortcut"]
                },
                layer=LayerType.MCP,
            ),
            ToolDefinition(
                name="open_app",
                description=(
                    "Launch an application by name. Uses Win+S search to find and open the app."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Application name, e.g. 'Notepad', 'Chrome', 'Calculator'"}
                    },
                    "required": ["name"]
                },
                layer=LayerType.MCP,
            ),
            ToolDefinition(
                name="drag_and_drop",
                description=(
                    "Drag from one screen position to another. Use for moving files, "
                    "resizing windows, reordering items, or any drag interaction. "
                    "Specify start (from_x, from_y) and end (to_x, to_y) coordinates."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "from_x": {"type": "integer", "description": "Starting X coordinate"},
                        "from_y": {"type": "integer", "description": "Starting Y coordinate"},
                        "to_x": {"type": "integer", "description": "Destination X coordinate"},
                        "to_y": {"type": "integer", "description": "Destination Y coordinate"},
                        "duration": {"type": "number", "description": "Drag duration in seconds (default 0.5)", "default": 0.5},
                        "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"}
                    },
                    "required": ["from_x", "from_y", "to_x", "to_y"]
                },
                layer=LayerType.MCP,
            ),
            ToolDefinition(
                name="hover",
                description=(
                    "Move the mouse cursor to coordinates [x, y] WITHOUT clicking. "
                    "Use to reveal tooltips, trigger hover menus, highlight items, "
                    "or position the cursor before a keyboard action."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer", "description": "X coordinate to hover over"},
                        "y": {"type": "integer", "description": "Y coordinate to hover over"},
                        "duration": {"type": "number", "description": "Time to move cursor (seconds, default 0.3)", "default": 0.3}
                    },
                    "required": ["x", "y"]
                },
                layer=LayerType.MCP,
            ),
            ToolDefinition(
                name="wait",
                description=(
                    "Pause execution for the specified number of seconds. "
                    "Use between actions to wait for animations, page loads, "
                    "dialogs to appear, or apps to launch. Essential for timing."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "seconds": {"type": "number", "description": "Seconds to wait (0.1 to 30)", "default": 1.0}
                    },
                    "required": ["seconds"]
                },
                layer=LayerType.MCP,
            ),
            ToolDefinition(
                name="select_text",
                description=(
                    "Select text on screen using various methods: "
                    "'all' = Ctrl+A to select all, "
                    "'word' = double-click at coordinates to select a word, "
                    "'line' = triple-click at coordinates to select a line, "
                    "'range' = click at start, then shift+click at end."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "method": {"type": "string", "enum": ["all", "word", "line", "range"], "description": "Selection method"},
                        "x": {"type": "integer", "description": "X coordinate (for word/line/range start)"},
                        "y": {"type": "integer", "description": "Y coordinate (for word/line/range start)"},
                        "end_x": {"type": "integer", "description": "End X coordinate (for range selection)"},
                        "end_y": {"type": "integer", "description": "End Y coordinate (for range selection)"}
                    },
                    "required": ["method"]
                },
                layer=LayerType.MCP,
            ),
        ]

    def _ensure_ui_helper(self):
        """Lazy-initialize the UIAutomation helper."""
        if self._ui_helper is None:
            try:
                self._ui_helper = UIAutomationHelper()
            except Exception:
                self._ui_helper = None

    def _execute_snapshot(self, use_vision: bool = False) -> ToolResult:
        """Capture desktop state: focused window + interactive elements."""
        self._ensure_ui_helper()

        # Get screen size
        screen_w, screen_h = pyautogui.size()

        # Get focused window info
        focused_info = {"name": "Unknown"}
        if self._ui_helper:
            try:
                focused_info = self._ui_helper.get_focused_window()
            except Exception:
                pass

        # Get foreground window via win32
        try:
            import win32gui
            hwnd = win32gui.GetForegroundWindow()
            fg_title = win32gui.GetWindowText(hwnd)
            fg_rect = win32gui.GetWindowRect(hwnd)
        except Exception:
            fg_title = focused_info.get("name", "Unknown")
            fg_rect = (0, 0, screen_w, screen_h)

        # Get interactive elements
        elements = []
        if self._ui_helper:
            try:
                elements = self._ui_helper.get_interactive_elements()
            except Exception:
                pass

        # Build snapshot output
        parts = [
            f"=== Desktop Snapshot ===",
            f"Screen: {screen_w}x{screen_h}",
            f"Focused Window: {fg_title}",
            f"Window Position: left={fg_rect[0]}, top={fg_rect[1]}, right={fg_rect[2]}, bottom={fg_rect[3]}",
            f"",
            f"Interactive Elements ({len(elements)}):",
        ]

        for i, el in enumerate(elements):
            parts.append(
                f"  [{i}] {el['type']}: \"{el['name']}\" at ({el['x']}, {el['y']})"
            )

        output = "\n".join(parts)
        data = {
            "screen": {"width": screen_w, "height": screen_h},
            "focused_window": fg_title,
            "elements": elements,
        }

        # Optionally include screenshot
        screenshot_b64 = None
        if use_vision:
            try:
                img, _ = capture_screenshot()
                screenshot_b64 = screenshot_to_base64(img)
            except Exception:
                pass

        return ToolResult(
            status=ToolResultStatus.SUCCESS,
            output=output,
            data=data,
            screenshot_base64=screenshot_b64,
            layer_used="ui_automation",
        )

    def _execute_click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> ToolResult:
        """Click at screen coordinates."""
        try:
            pyautogui.click(x=x, y=y, button=button, clicks=clicks)
            action = "Double-clicked" if clicks == 2 else "Clicked"
            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output=f"{action} {button} button at ({x}, {y})",
                layer_used="ui_automation",
            )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Click failed: {str(e)}",
                layer_used="ui_automation",
            )

    def _execute_type_text(self, x: int, y: int, text: str, clear: bool = False, press_enter: bool = False) -> ToolResult:
        """Click on a text field and type text using clipboard paste for reliability."""
        try:
            import subprocess

            # Click to focus the field
            pyautogui.click(x=x, y=y)
            time.sleep(0.3)  # Longer delay for focus

            # Click again to ensure focus (some fields need double engagement)
            pyautogui.click(x=x, y=y)
            time.sleep(0.2)

            # Clear existing text if requested
            if clear:
                pyautogui.hotkey("ctrl", "a")
                time.sleep(0.15)
                pyautogui.press("delete")
                time.sleep(0.15)

            # Use clipboard-based typing for reliability
            # Save current clipboard content
            try:
                old_clipboard = subprocess.run(
                    ["powershell", "-command", "Get-Clipboard"],
                    capture_output=True, text=True, timeout=3
                ).stdout.strip()
            except Exception:
                old_clipboard = None

            # Copy our text to clipboard
            try:
                subprocess.run(
                    ["powershell", "-command", f"Set-Clipboard -Value '{text.replace(chr(39), chr(39)+chr(39))}'"],
                    capture_output=True, timeout=3
                )
                time.sleep(0.1)

                # Paste using Ctrl+V
                pyautogui.hotkey("ctrl", "v")
                time.sleep(0.2)
            except Exception:
                # Fallback to typewrite if clipboard fails
                if text.isascii():
                    pyautogui.typewrite(text, interval=0.03)
                else:
                    pyautogui.write(text)

            # Restore original clipboard
            if old_clipboard is not None:
                try:
                    subprocess.run(
                        ["powershell", "-command", f"Set-Clipboard -Value '{old_clipboard.replace(chr(39), chr(39)+chr(39))}'"],
                        capture_output=True, timeout=3
                    )
                except Exception:
                    pass

            # Press enter if requested
            if press_enter:
                time.sleep(0.2)
                pyautogui.press("enter")

            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output=f"Typed '{text[:50]}{'...' if len(text)>50 else ''}' at ({x}, {y})"
                       + (" [cleared first]" if clear else "")
                       + (" [pressed Enter]" if press_enter else ""),
                layer_used="ui_automation",
            )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Type failed: {str(e)}",
                layer_used="ui_automation",
            )

    def _execute_scroll(self, x: int, y: int, direction: str = "down", amount: int = 3) -> ToolResult:
        """Scroll at coordinates."""
        try:
            pyautogui.moveTo(x, y)
            time.sleep(0.1)

            if direction == "down":
                pyautogui.scroll(-amount)
            elif direction == "up":
                pyautogui.scroll(amount)
            elif direction == "left":
                pyautogui.hscroll(-amount)
            elif direction == "right":
                pyautogui.hscroll(amount)

            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output=f"Scrolled {direction} by {amount} at ({x}, {y})",
                layer_used="ui_automation",
            )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Scroll failed: {str(e)}",
                layer_used="ui_automation",
            )

    def _execute_keyboard_shortcut(self, shortcut: str) -> ToolResult:
        """Press a keyboard shortcut."""
        try:
            # Parse shortcut like "ctrl+shift+s" into individual keys
            keys = [k.strip().lower() for k in shortcut.split("+")]

            # Map common key names to pyautogui names
            key_map = {
                "ctrl": "ctrl", "control": "ctrl",
                "alt": "alt",
                "shift": "shift",
                "win": "win", "windows": "win", "super": "win",
                "enter": "enter", "return": "enter",
                "esc": "escape", "escape": "escape",
                "tab": "tab",
                "space": "space",
                "backspace": "backspace",
                "delete": "delete", "del": "delete",
                "up": "up", "down": "down", "left": "left", "right": "right",
                "home": "home", "end": "end",
                "pageup": "pageup", "pagedown": "pagedown",
                "f1": "f1", "f2": "f2", "f3": "f3", "f4": "f4",
                "f5": "f5", "f6": "f6", "f7": "f7", "f8": "f8",
                "f9": "f9", "f10": "f10", "f11": "f11", "f12": "f12",
            }

            mapped_keys = [key_map.get(k, k) for k in keys]

            if len(mapped_keys) == 1:
                pyautogui.press(mapped_keys[0])
            else:
                pyautogui.hotkey(*mapped_keys)

            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output=f"Pressed: {shortcut}",
                layer_used="ui_automation",
            )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Keyboard shortcut failed: {str(e)}",
                layer_used="ui_automation",
            )

    def _execute_open_app(self, name: str) -> ToolResult:
        """Open an application using Win+S search."""
        try:
            # Press Win+S to open search
            pyautogui.hotkey("win", "s")
            time.sleep(0.8)

            # Type the app name
            pyautogui.typewrite(name, interval=0.03)
            time.sleep(1.0)

            # Press Enter to launch the first result
            pyautogui.press("enter")
            time.sleep(0.5)

            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output=f"Launched application: {name} (via Windows Search)",
                layer_used="ui_automation",
            )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Failed to open app: {str(e)}",
                layer_used="ui_automation",
            )

    def _execute_drag_and_drop(self, from_x: int, from_y: int, to_x: int, to_y: int,
                                duration: float = 0.5, button: str = "left") -> ToolResult:
        """Drag from one position to another."""
        try:
            pyautogui.moveTo(from_x, from_y)
            time.sleep(0.1)
            pyautogui.mouseDown(button=button)
            time.sleep(0.1)
            pyautogui.moveTo(to_x, to_y, duration=duration)
            time.sleep(0.1)
            pyautogui.mouseUp(button=button)

            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output=f"Dragged from ({from_x}, {from_y}) to ({to_x}, {to_y})",
                layer_used="ui_automation",
            )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Drag failed: {str(e)}",
                layer_used="ui_automation",
            )

    def _execute_hover(self, x: int, y: int, duration: float = 0.3) -> ToolResult:
        """Move cursor to coordinates without clicking."""
        try:
            pyautogui.moveTo(x, y, duration=duration)
            time.sleep(0.3)  # Wait for tooltip/hover effect

            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output=f"Hovered at ({x}, {y})",
                layer_used="ui_automation",
            )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Hover failed: {str(e)}",
                layer_used="ui_automation",
            )

    def _execute_wait(self, seconds: float = 1.0) -> ToolResult:
        """Pause execution for a specified duration."""
        seconds = max(0.1, min(30.0, seconds))  # Clamp to 0.1-30s
        time.sleep(seconds)
        return ToolResult(
            status=ToolResultStatus.SUCCESS,
            output=f"Waited {seconds} seconds",
            layer_used="ui_automation",
        )

    def _execute_select_text(self, method: str, x: int = 0, y: int = 0,
                              end_x: int = 0, end_y: int = 0) -> ToolResult:
        """Select text using various methods."""
        try:
            if method == "all":
                pyautogui.hotkey("ctrl", "a")
                return ToolResult(
                    status=ToolResultStatus.SUCCESS,
                    output="Selected all text (Ctrl+A)",
                    layer_used="ui_automation",
                )
            elif method == "word":
                pyautogui.click(x=x, y=y, clicks=2)
                return ToolResult(
                    status=ToolResultStatus.SUCCESS,
                    output=f"Double-clicked to select word at ({x}, {y})",
                    layer_used="ui_automation",
                )
            elif method == "line":
                pyautogui.click(x=x, y=y, clicks=3)
                return ToolResult(
                    status=ToolResultStatus.SUCCESS,
                    output=f"Triple-clicked to select line at ({x}, {y})",
                    layer_used="ui_automation",
                )
            elif method == "range":
                pyautogui.click(x=x, y=y)
                time.sleep(0.1)
                pyautogui.keyDown("shift")
                pyautogui.click(x=end_x, y=end_y)
                pyautogui.keyUp("shift")
                return ToolResult(
                    status=ToolResultStatus.SUCCESS,
                    output=f"Selected range from ({x}, {y}) to ({end_x}, {end_y})",
                    layer_used="ui_automation",
                )
            else:
                return ToolResult(
                    status=ToolResultStatus.ERROR,
                    output="",
                    error=f"Unknown selection method: {method}. Use 'all', 'word', 'line', or 'range'.",
                    layer_used="ui_automation",
                )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Select failed: {str(e)}",
                layer_used="ui_automation",
            )

