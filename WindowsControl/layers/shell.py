"""
Layer 1: Shell — PowerShell/CMD command execution with safety guards.
Handles system commands, file operations, registry access, and more.
"""

import subprocess
import re
from tools.base import BaseTool, ToolDefinition, ToolResult, ToolResultStatus, LayerType
from config import SHELL_TIMEOUT, SHELL_EXECUTABLE, DESTRUCTIVE_COMMANDS_BLOCKLIST, REQUIRE_CONFIRMATION_FOR_DESTRUCTIVE


class ShellLayer(BaseTool):
    """Execute shell commands via PowerShell with safety checks."""

    def __init__(self):
        super().__init__()
        self._definitions = [
            ToolDefinition(
                name="run_shell",
                description=(
                    "Execute a PowerShell command and return the output. "
                    "Use for file operations, system info, registry access, "
                    "network commands, and any system-level task. "
                    "The command runs in PowerShell on Windows."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The PowerShell command to execute."
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Timeout in seconds (default: 30).",
                            "default": SHELL_TIMEOUT
                        }
                    },
                    "required": ["command"]
                },
                layer=LayerType.SHELL,
                is_destructive=False,
                requires_confirmation=False,
            ),
        ]

    def _is_destructive(self, command: str) -> bool:
        """Check if a command matches the blocklist of destructive commands."""
        cmd_lower = command.lower().strip()
        for blocked in DESTRUCTIVE_COMMANDS_BLOCKLIST:
            if blocked.lower() in cmd_lower:
                return True
        return False

    def _execute_run_shell(self, command: str, timeout: int = SHELL_TIMEOUT) -> ToolResult:
        """Execute a PowerShell command."""
        # Safety check
        if REQUIRE_CONFIRMATION_FOR_DESTRUCTIVE and self._is_destructive(command):
            return ToolResult(
                status=ToolResultStatus.NEEDS_CONFIRMATION,
                output=f"⚠️ Destructive command detected: `{command}`\nPlease confirm execution.",
                data={"command": command},
                layer_used="shell",
            )

        try:
            result = subprocess.run(
                [SHELL_EXECUTABLE, "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=None,
            )

            output = result.stdout.strip()
            error = result.stderr.strip()

            if result.returncode != 0:
                return ToolResult(
                    status=ToolResultStatus.ERROR,
                    output=output or error,
                    error=f"Command exited with code {result.returncode}: {error}",
                    layer_used="shell",
                    data={"returncode": result.returncode},
                )

            # Combine stdout and any non-error stderr (some commands write info to stderr)
            full_output = output
            if error and result.returncode == 0:
                full_output += f"\n[STDERR]: {error}"

            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                output=full_output or "(no output)",
                layer_used="shell",
                data={"returncode": result.returncode},
            )

        except subprocess.TimeoutExpired:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Command timed out after {timeout}s: {command}",
                layer_used="shell",
            )
        except Exception as e:
            return ToolResult(
                status=ToolResultStatus.ERROR,
                output="",
                error=f"Shell execution failed: {str(e)}",
                layer_used="shell",
            )
