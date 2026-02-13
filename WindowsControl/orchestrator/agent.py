"""
Agent — Context-aware desktop control agent.
Gathers desktop state before each task so the LLM always knows what's running.
"""

import json
import time
import requests
from typing import Optional
from tools.base import BaseTool, ToolDefinition, ToolResult, ToolResultStatus
from orchestrator.router import route_task, get_routing_hint
from config import GEMINI_API_BASE, GEMINI_API_KEY, GEMINI_MODEL, AGENT_MAX_ITERATIONS


SYSTEM_PROMPT = """You are a skilled Windows desktop user with full control over the computer. Think and act like a real person sitting at this desk — aware of everything on screen, efficient with your actions, and careful not to disrupt anything.

## Your Mindset:
- You can SEE the desktop. Before each task, you receive a live snapshot of all open windows, the focused app, and interactive UI elements with their exact coordinates.
- You REMEMBER what's already open. Don't open a second browser if one is already running — switch to it. Don't launch apps that are already visible.
- You PLAN before acting. Read the desktop state, identify the right window/tab, then execute precisely.
- You CLEAN UP after yourself only when appropriate. Don't close windows the user had open before your task.

## How to Work:

### Before Acting:
- Study the [DESKTOP STATE] provided with the user's task. It shows every open window and UI element.
- Identify which window/app is relevant. Is the browser already open? Is there an existing tab you can use?
- Plan the minimum set of actions needed.

### Navigation Intelligence:
- If the user says "open YouTube" and Chrome is already running → use `window_control` to bring Chrome to foreground, then navigate to YouTube.
- If Notepad is already open with content → don't open a new Notepad unless asked.
- If a file is already open in the right app → just switch to that window.
- Use `keyboard_shortcut` for efficient navigation: Ctrl+T (new tab), Ctrl+L (address bar), Alt+Tab (switch app).

### During Execution:
- Use `wait` after launches and page loads (1-3 seconds is usually enough).
- After clicking a UI element, take a new `snapshot` to see the updated state before proceeding.
- If an action fails, try an alternative approach (e.g., keyboard shortcut instead of clicking).
- Use coordinates from `snapshot` data — never guess positions.

### Coordinate Usage:
- The [DESKTOP STATE] includes interactive elements with exact (x, y) center coordinates.
- Match the element name/type to what you want to interact with, then use its coordinates.
- If the element isn't in the accessibility tree, use `find_element_visual` to locate it visually.

## Tool Reference:

**Screen Interaction:** `snapshot`, `click`, `type_text`, `scroll`, `keyboard_shortcut`, `open_app`, `drag_and_drop`, `hover`, `wait`, `select_text`

**Window & System:** `list_windows`, `window_control`, `window_move_resize`, `clipboard_op`, `process_manage`, `system_info`, `screen_info`, `open_url`, `file_operations`, `com_automate`

**Vision AI:** `screenshot_analyze`, `find_element_visual`, `verify_action`, `read_screen_text`, `wait_for_element`

**Shell:** `run_shell`

## Rules:
- NEVER guess coordinates — always use snapshot data or find_element_visual.
- Prefer `open_url` for URLs, `window_control` to switch windows, `keyboard_shortcut` for efficiency.
- Be concise in your final response — tell the user what you did and the result.

{routing_hint}"""


class Agent:
    """
    Context-aware desktop control agent.
    Maintains persistent conversation memory across all tasks in a session.
    """

    def __init__(self, tool_sets: list[BaseTool]):
        self._tool_sets = tool_sets
        self._tools: dict[str, BaseTool] = {}
        self._tool_definitions: list[ToolDefinition] = []
        # Persistent conversation history — survives across tasks
        self._messages: list[dict] = []
        self._system_prompt_base: str = ""
        self._task_count: int = 0
        self._action_log: list[dict] = []  # Detailed log of every action + result

        # Register all tools from all tool sets
        for tool_set in tool_sets:
            for defn in tool_set.get_definitions():
                self._tools[defn.name] = tool_set
                self._tool_definitions.append(defn)

    def _get_tool_functions(self) -> list[dict]:
        """Get all tool definitions in OpenAI function calling format."""
        return [defn.to_openai_function() for defn in self._tool_definitions]

    def _call_llm(self, messages: list[dict], tools: list[dict] = None) -> dict:
        """Call Gemini via the local Antigravity proxy."""
        url = f"{GEMINI_API_BASE}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {GEMINI_API_KEY}",
        }
        payload = {
            "model": GEMINI_MODEL,
            "messages": messages,
            "max_tokens": 8192,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                f"Cannot reach Gemini API at {GEMINI_API_BASE}. "
                f"Is the Antigravity proxy running on port 8888?"
            )
        except Exception as e:
            raise RuntimeError(f"LLM call failed: {str(e)}")

    def _execute_tool(self, tool_name: str, arguments: dict) -> ToolResult:
        """Execute a tool by name with given arguments."""
        tool_set = self._tools.get(tool_name)
        if not tool_set:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Unknown tool: {tool_name}",
            )
        return tool_set.execute(tool_name, **arguments)

    def _gather_desktop_context(self) -> str:
        """
        Auto-gather current desktop state before a task.
        Returns a formatted context string to inject into the user message.
        """
        parts = ["[DESKTOP STATE — live snapshot taken right now]"]

        # 1. Get open windows
        try:
            win_result = self._execute_tool("list_windows", {})
            if win_result.status == ToolResultStatus.SUCCESS:
                windows = win_result.data.get("windows", []) if win_result.data else []
                if windows:
                    parts.append(f"\nOpen Windows ({len(windows)}):")
                    for w in windows:
                        title = w.get("title", "Untitled")
                        hwnd = w.get("hwnd", "?")
                        rect = w.get("position", w.get("rect", {}))
                        pos = f"({rect.get('left', 0)},{rect.get('top', 0)} - {rect.get('right', 0)},{rect.get('bottom', 0)})" if rect else ""
                        parts.append(f"  • [{hwnd}] \"{title}\" {pos}")
        except Exception:
            pass

        # 3. Get clipboard content
        try:
            clip_result = self._execute_tool("clipboard_op", {"action": "get"})
            if clip_result.status == ToolResultStatus.SUCCESS and clip_result.output:
                clip_text = clip_result.output.replace("\n", " ")[:100]
                parts.append(f"\nClipboard: \"{clip_text}...\"")
        except Exception:
            pass

        return "\n".join(parts)

    def _build_action_summary(self) -> str:
        """Build a concise summary of all actions taken so far in this session."""
        if not self._action_log:
            return ""

        summary = ["\n[SESSION HISTORY]"]
        for entry in self._action_log:
            summary.append(f"- Task #{entry['task']} | {entry['tool']}({entry['args_brief']}) -> {entry['status']}")
        return "\n".join(summary)

    def _should_verify(self, tool_name: str) -> bool:
        """Check if a tool is state-changing and requires verification."""
        VERIFY_TOOLS = {
            "click", "type_text", "scroll", "drag_and_drop", "keyboard_shortcut",
            "open_app", "open_url", "window_control", "window_move_resize",
            "snap_window", "set_active_window", "switch_tab", "terminate_process",
            "select_text", "file_operations", "clipboard_op"
        }
        return tool_name in VERIFY_TOOLS

    def _capture_verification_state(self) -> str:
        """Capture the current desktop state for verification."""
        # Reuse the context gatherer but simpler formatting
        try:
            context = self._gather_desktop_context()
            # Strip the header to keep it compact
            lines = context.split("\n")
            # Return roughly the window list, clipboard, and focus info
            relevant_lines = [l for l in lines if "Active Window:" in l or "Open Windows:" in l or "Interactable Elements:" in l or "•" in l or "Clipboard:" in l]
            return "\n".join(relevant_lines)
        except Exception:
            return "State verification failed."

    def run(self, task: str, on_step: callable = None) -> str:
        """
        Execute a task using the ReAct agent loop.
        Maintains persistent conversation memory across tasks.
        """
        self._task_count += 1

        # Route the task to determine layer priority
        layers = route_task(task)
        routing_hint = get_routing_hint(task, layers)

        # Build system prompt with routing hint
        system_prompt = SYSTEM_PROMPT.format(routing_hint=routing_hint)

        # Auto-gather desktop context
        if on_step:
            on_step(0, "🔍 Gathering desktop context...", None)
        desktop_context = self._gather_desktop_context()

        # Build session memory summary
        session_memory = self._build_action_summary()

        # Build the user message with full context
        user_message = f"{desktop_context}{session_memory}\n\n[USER TASK #{self._task_count}]\n{task}"

        # On first task, initialize; on subsequent tasks, append to persistent history
        if self._task_count == 1:
            self._messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]
        else:
            # Update the system prompt in case routing changed
            self._messages[0] = {"role": "system", "content": system_prompt}
            # Append the new task as a new user message (conversation continues)
            self._messages.append({"role": "user", "content": user_message})

        tools = self._get_tool_functions()
        iteration = 0

        while iteration < AGENT_MAX_ITERATIONS:
            iteration += 1

            # Call LLM with the full persistent conversation
            try:
                response = self._call_llm(self._messages, tools)
            except RuntimeError as e:
                return f"❌ Agent error: {str(e)}"

            choice = response.get("choices", [{}])[0]
            message = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "")

            # If the model wants to call tools
            tool_calls = message.get("tool_calls", [])

            if tool_calls:
                # Add assistant message to persistent history
                self._messages.append(message)

                for tool_call in tool_calls:
                    func = tool_call.get("function", {})
                    tool_name = func.get("name", "")
                    try:
                        arguments = json.loads(func.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        arguments = {}

                    tool_call_id = tool_call.get("id", f"call_{iteration}")

                    # Notify callback
                    if on_step:
                        on_step(iteration, f"🔧 {tool_name}({arguments})", None)

                    # Execute the tool
                    result = self._execute_tool(tool_name, arguments)

                    # Handle destructive confirmation
                    if result.status == ToolResultStatus.NEEDS_CONFIRMATION:
                        if on_step:
                            on_step(iteration, f"⚠️ Needs confirmation", result)
                        result = ToolResult(
                            status=ToolResultStatus.ERROR,
                            output="Action was blocked because it requires user confirmation.",
                            error="Destructive action blocked. Ask the user for permission.",
                        )

                    # Notify callback with result
                    if on_step:
                        on_step(iteration, f"📋 {tool_name} → {result.status.value}", result)

                    # NEW: Auto-Verification
                    # If the tool changed state, capture the new state immediately so the LLM knows if it worked.
                    if result.status == ToolResultStatus.SUCCESS and self._should_verify(tool_name):
                        if on_step:
                            on_step(iteration, "  🔍 Verifying effect...", None)
                        
                        # Small sleep to let UI settle
                        time.sleep(1.0)
                        verification = self._capture_verification_state()
                        result.output += f"\n\n[POST-ACTION STATE]\n{verification}"

                    # Log the action with details for session memory
                    args_brief = ", ".join(f"{k}={repr(v)[:40]}" for k, v in arguments.items())
                    result_brief = (result.output or result.error or "")[:100].replace("\n", " ")
                    self._action_log.append({
                        "task": self._task_count,
                        "tool": tool_name,
                        "args_brief": args_brief[:80],
                        "status": result.status.value,
                        "result_brief": result_brief,
                    })

                    # Add tool result to persistent conversation
                    result_str = str(result)
                    self._messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": result_str,
                    })

            elif finish_reason == "stop" or not tool_calls:
                # Model is done — add its response to persistent history
                content = message.get("content", "")
                self._messages.append({"role": "assistant", "content": content or "Task completed."})

                if content:
                    return content
                else:
                    return "✅ Task completed (no additional message from agent)."

        return f"⚠️ Agent reached maximum iterations ({AGENT_MAX_ITERATIONS}). Task may be incomplete."

    def get_available_tools(self) -> list[str]:
        """Return names of all available tools."""
        return [d.name for d in self._tool_definitions]


