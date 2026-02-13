"""
Base tool interface for the hybrid Windows control system.
All tools follow this unified interface regardless of which layer they belong to.
"""

from dataclasses import dataclass, field
from typing import Any, Optional
from enum import Enum
import json


class LayerType(str, Enum):
    SHELL = "shell"
    MCP = "mcp"
    WIN32 = "win32"
    VISION = "vision"


class ToolResultStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    NEEDS_CONFIRMATION = "needs_confirmation"
    NEEDS_VISION_FALLBACK = "needs_vision_fallback"


@dataclass
class ToolResult:
    """Unified result from any tool execution."""
    status: ToolResultStatus
    output: str
    data: Optional[dict] = None
    screenshot_base64: Optional[str] = None
    error: Optional[str] = None
    layer_used: Optional[str] = None
    confidence: Optional[int] = None

    def to_dict(self) -> dict:
        result = {
            "status": self.status.value,
            "output": self.output,
        }
        if self.data:
            result["data"] = self.data
        if self.error:
            result["error"] = self.error
        if self.layer_used:
            result["layer_used"] = self.layer_used
        if self.confidence is not None:
            result["confidence"] = self.confidence
        return result

    def __str__(self) -> str:
        if self.status == ToolResultStatus.ERROR:
            return f"[ERROR] {self.error or self.output}"
        parts = [self.output]
        if self.data:
            import json
            try:
                data_str = json.dumps(self.data, default=str, ensure_ascii=False)
                parts.append(f"\n[DATA] {data_str}")
            except Exception:
                pass
        return "\n".join(parts)


@dataclass
class ToolDefinition:
    """Definition of a tool available to the agent."""
    name: str
    description: str
    parameters: dict  # JSON Schema format
    layer: LayerType
    is_destructive: bool = False
    requires_confirmation: bool = False

    def to_openai_function(self) -> dict:
        """Convert to OpenAI function calling format for the LLM."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }


class BaseTool:
    """Base class for all tools."""

    # Common parameter name aliases the LLM might use
    # Maps wrong_name → correct_name
    PARAM_ALIASES = {
        # window_control
        "operation": "action",
        "command": "action",
        "op": "action",
        "handle": "title",
        "hwnd": "title",
        "window_handle": "title",
        "window_title": "title",
        "window_name": "title",
        "window": "title",
        # keyboard_shortcut
        "key": "shortcut",
        "keys": "shortcut",
        "hotkey": "shortcut",
        "combo": "shortcut",
        # screenshot_analyze
        "prompt": "question",
        # verify_action
        "expected_outcome": "description",
        "expected": "description",
        "outcome": "description",
        "explanation": "question",
        # find_element_visual
        "element": "element_description",
        # click
        "btn": "button",
        # general
        "file": "path",
        "filename": "path",
        "filepath": "path",
        "cmd": "command",
        "dir": "directory",
        "wait_time": "seconds",
        "delay": "seconds",
        "duration": "seconds",
        "timeout_seconds": "timeout",
        "app": "name",
        "app_name": "name",
        "application": "name",
        "query": "text",
        "input": "text",
        "content": "text",
        "uri": "url",
        "link": "url",
        # snap_window
        "side": "position",
        "target": "title",
        "window_target": "title",
    }

    def __init__(self):
        self._definitions: list[ToolDefinition] = []

    def get_definitions(self) -> list[ToolDefinition]:
        """Return all tool definitions from this tool set."""
        return self._definitions

    def execute(self, tool_name: str, **kwargs) -> ToolResult:
        """Execute a tool by name with given arguments.
        
        Resilient to LLM parameter name variations — automatically remaps
        common aliases and drops unknown kwargs to prevent crashes.
        """
        import inspect

        method_name = f"_execute_{tool_name}"
        method = getattr(self, method_name, None)
        if method is None:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Unknown tool: {tool_name}",
            )

        # Get the method's accepted parameter names
        try:
            sig = inspect.signature(method)
            accepted_params = set(sig.parameters.keys()) - {"self"}
            has_var_keyword = any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in sig.parameters.values()
            )
        except (ValueError, TypeError):
            # Can't inspect — just pass through
            accepted_params = None
            has_var_keyword = True

        # If we can inspect the method, remap and filter kwargs
        if accepted_params is not None and not has_var_keyword:
            remapped = {}
            for key, value in kwargs.items():
                if key in accepted_params:
                    # Direct match — use as-is
                    remapped[key] = value
                elif key in self.PARAM_ALIASES:
                    # Known alias — remap to correct name
                    correct_name = self.PARAM_ALIASES[key]
                    if correct_name in accepted_params and correct_name not in remapped:
                        remapped[correct_name] = value
                # else: silently drop unknown params
            kwargs = remapped

        try:
            return method(**kwargs)
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Tool '{tool_name}' failed: {str(e)}",
                layer_used=self._definitions[0].layer.value if self._definitions else None,
            )

