"""
Layer 3: Win32 — Direct pywin32 automation for deep Windows control.
Covers COM automation, window management, clipboard, registry, and process control.
"""

import json
from tools.base import BaseTool, ToolDefinition, ToolResult, ToolResultStatus, LayerType

# pywin32 imports (conditional to avoid crash on non-Windows)
try:
    import win32gui
    import win32api
    import win32con
    import win32process
    import win32clipboard
    import win32com.client
    import psutil
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


class Win32Layer(BaseTool):
    """Direct Windows API automation via pywin32."""

    def __init__(self):
        super().__init__()
        if not HAS_WIN32:
            raise RuntimeError("pywin32 is not installed. Run: pip install pywin32")

        self._definitions = [
            ToolDefinition(
                name="list_windows",
                description=(
                    "List all visible windows with their titles, handles (HWND), PIDs, "
                    "and screen positions. Use this to find windows to interact with. "
                    "Returns structured data with window details."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "filter_title": {
                            "type": "string",
                            "description": "Optional: filter windows by title substring (case-insensitive)"
                        }
                    }
                },
                layer=LayerType.WIN32,
            ),
            ToolDefinition(
                name="get_active_window",
                description=(
                    "Get the currently active/foreground window's title, handle, PID, "
                    "and position. Use this to know what window you are currently interacting with."
                ),
                parameters={
                    "type": "object",
                    "properties": {}
                },
                layer=LayerType.WIN32,
            ),
            ToolDefinition(
                name="set_active_window",
                description=(
                    "Bring a specific window to the foreground/active state by its title. "
                    "Uses forced foreground switching to reliably activate the window even from "
                    "background processes. USE THIS before interacting with a specific app — "
                    "it guarantees the target window is on top and focused."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Window title substring (partial match, case-insensitive). E.g. 'Chrome', 'Notepad', 'YouTube'"
                        }
                    },
                    "required": ["title"]
                },
                layer=LayerType.WIN32,
            ),
            ToolDefinition(
                name="clipboard_op",
                description="Read or set the Windows clipboard content.",
                parameters={
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string", "enum": ["get", "set"], "description": "Operation mode"},
                        "text": {"type": "string", "description": "Text to set (required for 'set' mode)"}
                    },
                    "required": ["mode"]
                },
                layer=LayerType.WIN32,
            ),
            ToolDefinition(
                name="process_manage",
                description="List running processes or kill a process by name or PID.",
                parameters={
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string", "enum": ["list", "kill"]},
                        "name": {"type": "string", "description": "Process name (for list filter or kill)"},
                        "pid": {"type": "integer", "description": "Process ID (for kill)"},
                        "limit": {"type": "integer", "default": 20, "description": "Max processes to list"}
                    },
                    "required": ["mode"]
                },
                layer=LayerType.WIN32,
                is_destructive=True,
            ),
            ToolDefinition(
                name="com_automate",
                description=(
                    "Automate a COM application (e.g., Excel, Word, Outlook). "
                    "Specify the COM ProgID and method/property to call."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "prog_id": {
                            "type": "string",
                            "description": "COM ProgID, e.g. 'Excel.Application', 'Word.Application'"
                        },
                        "script": {
                            "type": "string",
                            "description": (
                                "Python-like script to execute against the COM object. "
                                "The variable 'app' refers to the COM application instance. "
                                "Example: 'app.Visible = True; wb = app.Workbooks.Add()'"
                            )
                        }
                    },
                    "required": ["prog_id", "script"]
                },
                layer=LayerType.WIN32,
                is_destructive=True,
            ),
            ToolDefinition(
                name="window_control",
                description=(
                    "Control a specific window by its title. Actions: "
                    "minimize, maximize, restore (bring back + focus), "
                    "close, foreground (bring to front without resizing), "
                    "focus (same as foreground), activate (same as foreground), "
                    "show, hide."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Window title substring (partial match, case-insensitive). E.g. 'Chrome', 'Notepad', 'YouTube'"
                        },
                        "action": {
                            "type": "string",
                            "enum": ["minimize", "maximize", "restore", "close", "foreground", "focus", "activate", "show", "hide"],
                            "description": "Action to perform on the window"
                        }
                    },
                    "required": ["title", "action"]
                },
                layer=LayerType.WIN32,
            ),
            ToolDefinition(
                name="switch_tab",
                description=(
                    "Switch to a specific tab in the active application (browser, IDE, etc). "
                    "Use tab_number (1-9) for direct tab switching (Ctrl+1-9), "
                    "'next' for Ctrl+Tab, 'prev' for Ctrl+Shift+Tab, "
                    "or 'new' for Ctrl+T to open a new tab, or 'close' for Ctrl+W."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["next", "prev", "new", "close"],
                            "description": "Tab action (use this OR tab_number, not both)"
                        },
                        "tab_number": {
                            "type": "integer",
                            "description": "Tab number 1-9 to switch to directly (Ctrl+1 through Ctrl+9)"
                        }
                    }
                },
                layer=LayerType.WIN32,
            ),
            ToolDefinition(
                name="snap_window",
                description=(
                    "Snap a window to half the screen (left or right), "
                    "useful for side-by-side workflows. Also supports top/bottom and corners."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Window title substring (partial match)"
                        },
                        "position": {
                            "type": "string",
                            "enum": ["left", "right", "top-left", "top-right", "bottom-left", "bottom-right"],
                            "description": "Screen position to snap the window to"
                        }
                    },
                    "required": ["title", "position"]
                },
                layer=LayerType.WIN32,
            ),
            ToolDefinition(
                name="system_info",
                description=(
                    "Get system information: CPU usage, RAM usage, disk space, "
                    "OS version, hostname, uptime. Use for system diagnostics."
                ),
                parameters={
                    "type": "object",
                    "properties": {}
                },
                layer=LayerType.WIN32,
            ),
            ToolDefinition(
                name="screen_info",
                description=(
                    "Get screen/display information: resolution, number of monitors, "
                    "DPI scaling, and cursor position."
                ),
                parameters={
                    "type": "object",
                    "properties": {}
                },
                layer=LayerType.WIN32,
            ),
            ToolDefinition(
                name="open_url",
                description=(
                    "Open a URL in the default web browser and bring it to the foreground. "
                    "The browser window will be automatically activated after opening."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Full URL to open, e.g. 'https://google.com'"}
                    },
                    "required": ["url"]
                },
                layer=LayerType.WIN32,
            ),
            ToolDefinition(
                name="window_move_resize",
                description=(
                    "Move and/or resize a window to exact pixel coordinates. "
                    "Use for arranging windows side by side, snapping to positions, "
                    "or setting exact window dimensions."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Window title (partial match)"},
                        "x": {"type": "integer", "description": "New X position (left edge)"},
                        "y": {"type": "integer", "description": "New Y position (top edge)"},
                        "width": {"type": "integer", "description": "New width in pixels"},
                        "height": {"type": "integer", "description": "New height in pixels"}
                    },
                    "required": ["title"]
                },
                layer=LayerType.WIN32,
            ),
            ToolDefinition(
                name="file_operations",
                description=(
                    "Read, write, or append to files. Safer and more structured than "
                    "shell commands for file content manipulation. "
                    "mode: 'read' = read file, 'write' = overwrite/create, 'append' = add to end."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string", "enum": ["read", "write", "append"], "description": "Operation mode"},
                        "path": {"type": "string", "description": "Absolute file path"},
                        "content": {"type": "string", "description": "Content to write/append (for write/append mode)"},
                        "encoding": {"type": "string", "description": "File encoding (default: utf-8)", "default": "utf-8"}
                    },
                    "required": ["mode", "path"]
                },
                layer=LayerType.WIN32,
            ),
        ]

    def _find_window_by_title(self, title_substring) -> int | None:
        """Find a window handle by partial title match. Also accepts integer hwnd directly."""
        # If an integer hwnd was passed, validate and return it directly
        if isinstance(title_substring, int):
            try:
                if win32gui.IsWindow(title_substring):
                    return title_substring
            except Exception:
                pass
            return None

        title_substring = str(title_substring).strip()
        result = []

        def enum_callback(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                window_title = win32gui.GetWindowText(hwnd).strip()
                if not window_title:
                    return
                
                # Strategy 1: Standard substring (search term inside window title)
                if title_substring.lower() in window_title.lower():
                    result.append(hwnd)

        win32gui.EnumWindows(enum_callback, None)
        if result:
            return result[0]

        # Strategy 2: Reverse containment (window title inside search term)
        # e.g. User asks for "YouTube - Google Chrome", window is "YouTube".
        # "YouTube" is in "YouTube - Google Chrome".
        best_match_hwnd = None
        best_match_len = 0

        def reverse_enum_callback(hwnd, _):
            nonlocal best_match_hwnd, best_match_len
            if win32gui.IsWindowVisible(hwnd):
                window_title = win32gui.GetWindowText(hwnd).strip()
                if not window_title:
                    return
                
                if window_title.lower() in title_substring.lower():
                    # Keep the longest title that matches (to avoid matching universal substrings)
                    if len(window_title) > best_match_len:
                        best_match_hwnd = hwnd
                        best_match_len = len(window_title)

        win32gui.EnumWindows(reverse_enum_callback, None)
        return best_match_hwnd

    def _force_foreground(self, hwnd: int):
        """Force a window to the foreground, even from a background process.
        
        Windows restricts SetForegroundWindow to the process that currently owns
        the foreground. This workaround attaches our thread to the foreground
        thread, which grants us permission to steal focus.
        """
        import time
        import ctypes

        try:
            # Get the current foreground window's thread
            fg_hwnd = win32gui.GetForegroundWindow()
            fg_thread, _ = win32process.GetWindowThreadProcessId(fg_hwnd)
            our_thread = win32api.GetCurrentThreadId()

            # If we're not the foreground thread, attach to it
            attached = False
            if fg_thread != our_thread:
                ctypes.windll.user32.AttachThreadInput(our_thread, fg_thread, True)
                attached = True

            # Now we have permission to set foreground
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            win32gui.BringWindowToTop(hwnd)

            # Detach threads
            if attached:
                ctypes.windll.user32.AttachThreadInput(our_thread, fg_thread, False)

            # Small delay to let Windows complete the switch
            time.sleep(0.3)
        except Exception:
            # Last resort: use Alt key trick
            try:
                win32api.keybd_event(0x12, 0, 0, 0)  # Alt press
                win32gui.SetForegroundWindow(hwnd)
                win32api.keybd_event(0x12, 0, 2, 0)  # Alt release
                time.sleep(0.3)
            except Exception:
                pass

    def _execute_list_windows(self, filter_title: str = None) -> ToolResult:
        """List all visible windows."""
        windows = []

        def enum_callback(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if not title:
                    return
                if filter_title and filter_title.lower() not in title.lower():
                    return

                try:
                    rect = win32gui.GetWindowRect(hwnd)
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    windows.append({
                        "hwnd": hwnd,
                        "title": title,
                        "pid": pid,
                        "position": {"left": rect[0], "top": rect[1],
                                     "right": rect[2], "bottom": rect[3]},
                    })
                except Exception:
                    pass

        win32gui.EnumWindows(enum_callback, None)

        if not windows:
            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output="No visible windows found." + (f" (filter: '{filter_title}')" if filter_title else ""),
                layer_used="win32",
            )

        lines = [f"Found {len(windows)} windows:"]
        for w in windows:
            lines.append(
                f"  [{w['hwnd']}] {w['title']} (PID: {w['pid']}, "
                f"pos: {w['position']['left']},{w['position']['top']} "
                f"-> {w['position']['right']},{w['position']['bottom']})"
            )

        return ToolResult(
            status=ToolResultStatus.SUCCESS,
            output="\n".join(lines),
            data={"windows": windows},
            layer_used="win32",
        )

    def _execute_get_active_window(self) -> ToolResult:
        """Get the currently active/foreground window information."""
        try:
            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            rect = win32gui.GetWindowRect(hwnd)

            info = {
                "hwnd": hwnd,
                "title": title or "(No title)",
                "pid": pid,
                "position": {
                    "left": rect[0], "top": rect[1],
                    "right": rect[2], "bottom": rect[3]
                }
            }

            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output=f"Active window: \"{title}\" [hwnd={hwnd}, PID={pid}] at ({rect[0]},{rect[1]} -> {rect[2]},{rect[3]})",
                data=info,
                layer_used="win32",
            )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Get active window failed: {str(e)}",
                layer_used="win32",
            )

    def _execute_set_active_window(self, title: str) -> ToolResult:
        """Bring a specific window to the foreground by its title."""
        hwnd = self._find_window_by_title(title)
        if not hwnd:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Window not found: '{title}'",
                layer_used="win32",
            )

        window_title = win32gui.GetWindowText(hwnd)
        try:
            self._force_foreground(hwnd)

            # Verify it actually came to the foreground
            import time
            time.sleep(0.2)
            fg = win32gui.GetForegroundWindow()
            is_foreground = (fg == hwnd)

            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output=f"Window '{window_title}' brought to foreground. {'✓ Verified active.' if is_foreground else '⚠ May need a moment to activate.'}",
                data={"hwnd": hwnd, "title": window_title, "is_foreground": is_foreground},
                layer_used="win32",
            )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Set active window failed: {str(e)}",
                layer_used="win32",
            )

    def _execute_switch_tab(self, action: str = None, tab_number: int = None) -> ToolResult:
        """Switch tabs in the active application."""
        import time
        try:
            if tab_number is not None:
                if 1 <= tab_number <= 9:
                    # Ctrl+1-9 for direct tab switching
                    win32api.keybd_event(0x11, 0, 0, 0)  # Ctrl press
                    key_code = 0x30 + tab_number  # VK_0 + number
                    win32api.keybd_event(key_code, 0, 0, 0)
                    win32api.keybd_event(key_code, 0, 2, 0)  # key release
                    win32api.keybd_event(0x11, 0, 2, 0)  # Ctrl release
                    time.sleep(0.3)
                    return ToolResult(
                        status=ToolResultStatus.SUCCESS,
                        output=f"Switched to tab {tab_number} (Ctrl+{tab_number})",
                        layer_used="win32",
                    )
                else:
                    return ToolResult(
                        status=ToolResultStatus.ERROR,
                        output="",
                        error="Tab number must be between 1 and 9",
                        layer_used="win32",
                    )

            if action == "next":
                win32api.keybd_event(0x11, 0, 0, 0)  # Ctrl
                win32api.keybd_event(0x09, 0, 0, 0)  # Tab
                win32api.keybd_event(0x09, 0, 2, 0)
                win32api.keybd_event(0x11, 0, 2, 0)
            elif action == "prev":
                win32api.keybd_event(0x11, 0, 0, 0)  # Ctrl
                win32api.keybd_event(0x10, 0, 0, 0)  # Shift
                win32api.keybd_event(0x09, 0, 0, 0)  # Tab
                win32api.keybd_event(0x09, 0, 2, 0)
                win32api.keybd_event(0x10, 0, 2, 0)
                win32api.keybd_event(0x11, 0, 2, 0)
            elif action == "new":
                win32api.keybd_event(0x11, 0, 0, 0)  # Ctrl
                win32api.keybd_event(0x54, 0, 0, 0)  # T
                win32api.keybd_event(0x54, 0, 2, 0)
                win32api.keybd_event(0x11, 0, 2, 0)
            elif action == "close":
                win32api.keybd_event(0x11, 0, 0, 0)  # Ctrl
                win32api.keybd_event(0x57, 0, 0, 0)  # W
                win32api.keybd_event(0x57, 0, 2, 0)
                win32api.keybd_event(0x11, 0, 2, 0)
            else:
                return ToolResult(
                    status=ToolResultStatus.ERROR,
                    output="",
                    error=f"Unknown tab action: {action}. Use 'next', 'prev', 'new', 'close', or provide tab_number.",
                    layer_used="win32",
                )

            time.sleep(0.3)
            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output=f"Tab action '{action}' performed.",
                layer_used="win32",
            )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Switch tab failed: {str(e)}",
                layer_used="win32",
            )

    def _execute_snap_window(self, title: str, position: str) -> ToolResult:
        """Snap a window to a screen position (left, right, or corner)."""
        try:
            # Get screen dimensions
            screen_w = win32api.GetSystemMetrics(0)  # SM_CXSCREEN
            screen_h = win32api.GetSystemMetrics(1)  # SM_CYSCREEN

            # Calculate target rectangle based on position
            positions = {
                "left":         (0, 0, screen_w // 2, screen_h),
                "right":        (screen_w // 2, 0, screen_w // 2, screen_h),
                "top-left":     (0, 0, screen_w // 2, screen_h // 2),
                "top-right":    (screen_w // 2, 0, screen_w // 2, screen_h // 2),
                "bottom-left":  (0, screen_h // 2, screen_w // 2, screen_h // 2),
                "bottom-right": (screen_w // 2, screen_h // 2, screen_w // 2, screen_h // 2),
            }

            if position not in positions:
                return ToolResult(
                    status=ToolResultStatus.ERROR,
                    output="",
                    error=f"Unknown position: {position}. Use: {', '.join(positions.keys())}",
                    layer_used="win32",
                )

            x, y, w, h = positions[position]
            return self._execute_window_move_resize(title=title, x=x, y=y, width=w, height=h)
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Snap window failed: {str(e)}",
                layer_used="win32",
            )

    def _execute_clipboard_op(self, mode: str, text: str = None) -> ToolResult:
        """Read or set clipboard content."""
        try:
            if mode == "get":
                win32clipboard.OpenClipboard()
                try:
                    if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                        data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                        return ToolResult(
                            status=ToolResultStatus.SUCCESS,
                            output=f"Clipboard content:\n{data}",
                            data={"content": data},
                            layer_used="win32",
                        )
                    else:
                        return ToolResult(
                            status=ToolResultStatus.SUCCESS,
                            output="Clipboard is empty or contains non-text data.",
                            layer_used="win32",
                        )
                finally:
                    win32clipboard.CloseClipboard()

            elif mode == "set":
                if text is None:
                    return ToolResult(
                        status=ToolResultStatus.ERROR,
                        output="",
                        error="'text' parameter required for set mode.",
                        layer_used="win32",
                    )
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
                    return ToolResult(
                        status=ToolResultStatus.SUCCESS,
                        output=f"Clipboard set to: {text[:200]}{'...' if len(text) > 200 else ''}",
                        layer_used="win32",
                    )
                finally:
                    win32clipboard.CloseClipboard()

        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Clipboard error: {str(e)}",
                layer_used="win32",
            )

    def _execute_process_manage(self, mode: str, name: str = None, pid: int = None, limit: int = 20) -> ToolResult:
        """List or kill processes."""
        if mode == "list":
            processes = []
            for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
                try:
                    info = proc.info
                    if name and name.lower() not in info["name"].lower():
                        continue
                    mem_mb = info["memory_info"].rss / (1024 * 1024) if info["memory_info"] else 0
                    processes.append({
                        "pid": info["pid"],
                        "name": info["name"],
                        "memory_mb": round(mem_mb, 1),
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            processes.sort(key=lambda x: x["memory_mb"], reverse=True)
            processes = processes[:limit]

            lines = [f"Top {len(processes)} processes" + (f" matching '{name}'" if name else "") + ":"]
            for p in processes:
                lines.append(f"  PID {p['pid']:>6} | {p['name']:<30} | {p['memory_mb']:>8.1f} MB")

            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output="\n".join(lines),
                data={"processes": processes},
                layer_used="win32",
            )

        elif mode == "kill":
            if not pid and not name:
                return ToolResult(
                    status=ToolResultStatus.ERROR,
                    output="",
                    error="Must provide 'pid' or 'name' to kill.",
                    layer_used="win32",
                )

            try:
                if pid:
                    p = psutil.Process(pid)
                    p_name = p.name()
                    p.terminate()
                    return ToolResult(
                        status=ToolResultStatus.SUCCESS,
                        output=f"Terminated process: {p_name} (PID: {pid})",
                        layer_used="win32",
                    )
                else:
                    killed = []
                    for proc in psutil.process_iter(["pid", "name"]):
                        if proc.info["name"].lower() == name.lower():
                            proc.terminate()
                            killed.append(proc.info["pid"])
                    return ToolResult(
                        status=ToolResultStatus.SUCCESS,
                        output=f"Terminated {len(killed)} processes named '{name}': PIDs {killed}",
                        layer_used="win32",
                    )

            except Exception as e:
                return ToolResult(
                    status=ToolResultStatus.ERROR,
                    output="",
                    error=f"Kill failed: {str(e)}",
                    layer_used="win32",
                )

    def _execute_com_automate(self, prog_id: str, script: str) -> ToolResult:
        """Execute COM automation script."""
        try:
            app = win32com.client.Dispatch(prog_id)
            # Create a safe execution environment
            local_vars = {"app": app, "result": None}
            exec(script, {"__builtins__": {}}, local_vars)

            result_val = local_vars.get("result")
            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output=f"COM automation completed on {prog_id}. Result: {result_val}",
                data={"prog_id": prog_id, "result": str(result_val) if result_val else None},
                layer_used="win32",
            )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"COM automation failed ({prog_id}): {str(e)}",
                layer_used="win32",
            )

    def _execute_window_control(self, title: str, action: str) -> ToolResult:
        """Control a window by its title."""
        hwnd = self._find_window_by_title(title)
        if not hwnd:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Window not found: '{title}'",
                layer_used="win32",
            )

        window_title = win32gui.GetWindowText(hwnd)
        try:
            if action == "minimize":
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            elif action == "maximize":
                win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
                self._force_foreground(hwnd)
            elif action == "restore":
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                self._force_foreground(hwnd)
            elif action == "close":
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            elif action in ("foreground", "focus", "activate"):
                self._force_foreground(hwnd)
            elif action == "show":
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            elif action == "hide":
                win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
            else:
                return ToolResult(
                    status=ToolResultStatus.ERROR,
                    output="",
                    error=f"Unknown action: {action}",
                    layer_used="win32",
                )

            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output=f"Window '{window_title}': {action} completed.",
                layer_used="win32",
            )

        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Window control failed: {str(e)}",
                layer_used="win32",
            )

    def _execute_system_info(self) -> ToolResult:
        """Get system information."""
        import platform
        import datetime

        try:
            cpu_percent = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("C:\\")
            boot_time = datetime.datetime.fromtimestamp(psutil.boot_time())
            uptime = datetime.datetime.now() - boot_time

            info = {
                "os": f"{platform.system()} {platform.release()} ({platform.version()})",
                "hostname": platform.node(),
                "cpu_percent": cpu_percent,
                "cpu_cores": psutil.cpu_count(),
                "ram_total_gb": round(mem.total / (1024**3), 1),
                "ram_used_gb": round(mem.used / (1024**3), 1),
                "ram_percent": mem.percent,
                "disk_total_gb": round(disk.total / (1024**3), 1),
                "disk_used_gb": round(disk.used / (1024**3), 1),
                "disk_percent": disk.percent,
                "uptime": str(uptime).split('.')[0],
            }

            lines = [
                f"OS: {info['os']}",
                f"Hostname: {info['hostname']}",
                f"CPU: {info['cpu_percent']}% ({info['cpu_cores']} cores)",
                f"RAM: {info['ram_used_gb']}/{info['ram_total_gb']} GB ({info['ram_percent']}%)",
                f"Disk (C:): {info['disk_used_gb']}/{info['disk_total_gb']} GB ({info['disk_percent']}%)",
                f"Uptime: {info['uptime']}",
            ]

            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output="\n".join(lines),
                data=info,
                layer_used="win32",
            )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"System info failed: {str(e)}",
                layer_used="win32",
            )

    def _execute_screen_info(self) -> ToolResult:
        """Get screen/display information."""
        try:
            import ctypes

            # Primary monitor resolution
            user32 = ctypes.windll.user32
            screen_w = user32.GetSystemMetrics(0)
            screen_h = user32.GetSystemMetrics(1)

            # DPI
            try:
                dpi = user32.GetDpiForSystem()
                scale = round(dpi / 96 * 100)
            except Exception:
                dpi = 96
                scale = 100

            # Cursor position
            cursor = win32api.GetCursorPos()

            # Number of monitors
            num_monitors = user32.GetSystemMetrics(80)  # SM_CMONITORS

            # Virtual screen (all monitors combined)
            virt_w = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
            virt_h = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
            virt_x = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
            virt_y = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN

            info = {
                "primary_width": screen_w,
                "primary_height": screen_h,
                "dpi": dpi,
                "scale_percent": scale,
                "monitors": num_monitors,
                "cursor_x": cursor[0],
                "cursor_y": cursor[1],
                "virtual_screen": {"x": virt_x, "y": virt_y, "width": virt_w, "height": virt_h},
            }

            lines = [
                f"Primary Display: {screen_w}x{screen_h}",
                f"DPI: {dpi} (Scale: {scale}%)",
                f"Monitors: {num_monitors}",
                f"Cursor: ({cursor[0]}, {cursor[1]})",
                f"Virtual Screen: {virt_w}x{virt_h} at ({virt_x}, {virt_y})",
            ]

            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output="\n".join(lines),
                data=info,
                layer_used="win32",
            )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Screen info failed: {str(e)}",
                layer_used="win32",
            )

    def _execute_open_url(self, url: str) -> ToolResult:
        """Open a URL in the default browser and bring it to foreground."""
        import webbrowser
        try:
            webbrowser.open(url)
            # Wait for browser to open and page to start loading
            import time
            time.sleep(2)

            # Try to bring the browser to the foreground
            try:
                # Look for common browser windows
                for browser_hint in ["Chrome", "Firefox", "Edge", "Brave", "Opera", "Safari"]:
                    hwnd = self._find_window_by_title(browser_hint)
                    if hwnd:
                        self._force_foreground(hwnd)
                        break
            except Exception:
                pass

            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output=f"Opened URL in default browser: {url} (browser brought to foreground)",
                layer_used="win32",
            )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Failed to open URL: {str(e)}",
                layer_used="win32",
            )

    def _execute_window_move_resize(self, title: str, x: int = None, y: int = None,
                                     width: int = None, height: int = None) -> ToolResult:
        """Move and/or resize a window."""
        hwnd = self._find_window_by_title(title)
        if not hwnd:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Window not found: '{title}'",
                layer_used="win32",
            )

        try:
            # Get current position if we need to keep any values
            rect = win32gui.GetWindowRect(hwnd)
            cur_x, cur_y = rect[0], rect[1]
            cur_w, cur_h = rect[2] - rect[0], rect[3] - rect[1]

            new_x = x if x is not None else cur_x
            new_y = y if y is not None else cur_y
            new_w = width if width is not None else cur_w
            new_h = height if height is not None else cur_h

            # Restore if maximized
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.MoveWindow(hwnd, new_x, new_y, new_w, new_h, True)

            window_title = win32gui.GetWindowText(hwnd)
            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output=f"Window '{window_title}' moved to ({new_x}, {new_y}) size {new_w}x{new_h}",
                layer_used="win32",
            )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Window move/resize failed: {str(e)}",
                layer_used="win32",
            )

    def _execute_file_operations(self, mode: str, path: str, content: str = None,
                                  encoding: str = "utf-8") -> ToolResult:
        """Read, write, or append to files."""
        try:
            if mode == "read":
                with open(path, "r", encoding=encoding) as f:
                    data = f.read()
                preview = data[:2000]
                truncated = len(data) > 2000
                return ToolResult(
                    status=ToolResultStatus.SUCCESS,
                    output=f"File: {path} ({len(data)} chars)\n---\n{preview}{'...' if truncated else ''}",
                    data={"path": path, "size": len(data), "truncated": truncated},
                    layer_used="win32",
                )
            elif mode == "write":
                if content is None:
                    return ToolResult(
                        status=ToolResultStatus.ERROR,
                        output="",
                        error="'content' parameter required for write mode.",
                        layer_used="win32",
                    )
                with open(path, "w", encoding=encoding) as f:
                    f.write(content)
                return ToolResult(
                    status=ToolResultStatus.SUCCESS,
                    output=f"Wrote {len(content)} chars to {path}",
                    layer_used="win32",
                )
            elif mode == "append":
                if content is None:
                    return ToolResult(
                        status=ToolResultStatus.ERROR,
                        output="",
                        error="'content' parameter required for append mode.",
                        layer_used="win32",
                    )
                with open(path, "a", encoding=encoding) as f:
                    f.write(content)
                return ToolResult(
                    status=ToolResultStatus.SUCCESS,
                    output=f"Appended {len(content)} chars to {path}",
                    layer_used="win32",
                )
            else:
                return ToolResult(
                    status=ToolResultStatus.ERROR,
                    output="",
                    error=f"Unknown mode: {mode}",
                    layer_used="win32",
                )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"File operation failed: {str(e)}",
                layer_used="win32",
            )

