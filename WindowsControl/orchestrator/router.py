"""
Task Router — Heuristic pre-routing to decide which layer handles each task.
This runs BEFORE the LLM agent loop to narrow down the tool set.
"""

import re
from tools.base import LayerType


# Keyword-based patterns for routing
SHELL_PATTERNS = [
    r"\b(file|folder|directory|path|copy|move|delete|rename|create|mkdir|list\s+files)\b",
    r"\b(registry|reg\s+(?:add|delete|query)|regedit)\b",
    r"\b(service|start-service|stop-service|restart-service)\b",
    r"\b(network|ip|ping|ipconfig|netstat|dns|curl|wget)\b",
    r"\b(install|uninstall|pip|npm|choco)\b",
    r"\b(environment|env\s+var|setx|path\s+variable)\b",
    r"\b(disk|storage|space|drive)\b",
    r"\b(powershell|cmd|command|terminal|shell|script)\b",
]

UI_PATTERNS = [
    r"\b(click|tap|press|button|menu|dropdown|select|checkbox)\b",
    r"\b(type|enter|input|fill|write\s+in|search\s+bar|text\s+field)\b",
    r"\b(scroll|swipe|drag|drop|drag.and.drop|move\s+to)\b",
    r"\b(open|launch|start|run)\s+\w+\s*(app|application|program)?\b",
    r"\b(window|tab|dialog|popup|modal)\b",
    r"\b(navigate|go\s+to|browse|url|website)\b",
    r"\b(screenshot|screen|desktop|taskbar|notification)\b",
    r"\b(hover|tooltip|mouseover|mouse\s+over)\b",
    r"\b(wait|pause|delay|sleep)\b",
    r"\b(select\s+all|triple.click|double.click|highlight\s+text)\b",
]

WIN32_PATTERNS = [
    r"\b(excel|spreadsheet|workbook|worksheet|cell|formula)\b",
    r"\b(word|document|docx|paragraph|heading)\b",
    r"\b(outlook|email|mail|send\s+email|inbox)\b",
    r"\b(powerpoint|pptx|slide|presentation)\b",
    r"\b(system\s+info|cpu|ram|memory|uptime|hostname)\b",
    r"\b(resolution|dpi|screen\s+size|monitor|display)\b",
    r"\b(open\s+url|open\s+link|browse\s+to|go\s+to\s+http)\b",
    r"\b(resize\s+window|move\s+window|arrange|tile|snap|side\s+by\s+side)\b",
    r"\b(read\s+file|write\s+file|save\s+to\s+file|append|file\s+content)\b",
    r"\b(clipboard|copy|paste)\b",
    r"\b(process|kill|terminate|pid)\b",
]

VISION_PATTERNS = [
    r"\b(what\s+do\s+you\s+see|what\s+is\s+on\s+screen|look\s+at)\b",
    r"\b(find|locate|where\s+is|identify)\b.*\b(button|icon|image|element|ui)\b",
    r"\b(verify|check|confirm|validate)\b.*\b(screen|visible|appeared|opened)\b",
    r"\b(screenshot|capture|screen\s+grab)\b",
    r"\b(read\s+text|ocr|extract\s+text|what\s+does\s+it\s+say)\b",
    r"\b(wait\s+for|until|appears|shows\s+up|loading)\b",
]


def _match_patterns(text: str, patterns: list[str]) -> int:
    """Count how many patterns match the text."""
    text_lower = text.lower()
    score = 0
    for pattern in patterns:
        if re.search(pattern, text_lower):
            score += 1
    return score


def route_task(task: str) -> list[LayerType]:
    """
    Determine the priority order of layers for a given task.
    Returns a list of LayerTypes in order of preference.

    The LLM agent will see tools from all layers but will be
    hinted to prefer tools from the first layer.
    """
    scores = {
        LayerType.SHELL: _match_patterns(task, SHELL_PATTERNS),
        LayerType.MCP: _match_patterns(task, UI_PATTERNS),
        LayerType.WIN32: _match_patterns(task, WIN32_PATTERNS),
        LayerType.VISION: _match_patterns(task, VISION_PATTERNS),
    }

    # Sort by score descending, keeping all layers
    sorted_layers = sorted(scores.keys(), key=lambda l: scores[l], reverse=True)

    # If no strong signal, default to MCP > Shell > Vision > Win32
    if all(s == 0 for s in scores.values()):
        return [LayerType.MCP, LayerType.SHELL, LayerType.VISION, LayerType.WIN32]

    return sorted_layers


def get_routing_hint(task: str, layers: list[LayerType]) -> str:
    """Generate a hint for the LLM about which layer to prefer."""
    primary = layers[0]
    hints = {
        LayerType.SHELL: "This task is best handled with shell commands. Prefer the run_shell tool.",
        LayerType.MCP: (
            "This task involves UI interaction. Prefer snapshot/click/type/hover/drag tools. "
            "Use snapshot first to understand the screen. Use wait between actions for timing."
        ),
        LayerType.WIN32: (
            "This task involves system/window management. Available: get_active_window, set_active_window, "
            "window_control, switch_tab, snap_window, window_move_resize, "
            "com_automate, system_info, screen_info, open_url, file_operations, clipboard_op, process_manage."
        ),
        LayerType.VISION: (
            "This task requires visual understanding. Use screenshot_analyze, find_element_visual, "
            "read_screen_text (OCR), or wait_for_element (polling)."
        ),
    }
    return hints.get(primary, "")
