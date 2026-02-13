# Hybrid Windows Control System
# Configuration

import os

# Gemini API via Antigravity Proxy
GEMINI_API_BASE = os.getenv("GEMINI_API_BASE", "http://localhost:8888/v1")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "admin123")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")

# Windows-MCP
MCP_COMMAND = os.getenv("MCP_COMMAND", "uvx")
MCP_ARGS = ["windows-mcp"]
MCP_TRANSPORT = "stdio"  # "stdio" | "streamable-http"
MCP_HTTP_PORT = 9000

# Agent settings
AGENT_MAX_ITERATIONS = 25
VISION_CONFIDENCE_THRESHOLD = 70
SCREENSHOT_MAX_WIDTH = 1920
SCREENSHOT_MAX_HEIGHT = 1080

# Safety
REQUIRE_CONFIRMATION_FOR_DESTRUCTIVE = True
DESTRUCTIVE_COMMANDS_BLOCKLIST = [
    "format", "del /s", "rm -rf", "rd /s", "rmdir",
    "reg delete", "shutdown", "restart-computer",
]

# Shell
SHELL_TIMEOUT = 30
SHELL_EXECUTABLE = "powershell.exe"

# Gesture / Speech Server (vvc14/Voice_and_Gesture_Control_UI)
GESTURE_WS_URL = os.getenv("GESTURE_WS_URL", "ws://localhost:8000/ws/gestures")
SPEECH_WS_URL = os.getenv("SPEECH_WS_URL", "ws://localhost:8000/ws/speech")
GESTURE_SERVER_HTTP = os.getenv("GESTURE_SERVER_HTTP", "http://localhost:8000")

# Screen resolution for normalised → pixel coordinate conversion
SCREEN_WIDTH = int(os.getenv("SCREEN_WIDTH", "1920"))
SCREEN_HEIGHT = int(os.getenv("SCREEN_HEIGHT", "1080"))
