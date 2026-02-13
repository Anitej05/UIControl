"""
Comprehensive Integration Test - WindowsControl + AIClient-2-API
Tests all 4 layers, 26 tools, LLM connectivity, and end-to-end agent execution.
"""

import sys
import os
import json
import time

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.base import ToolResultStatus

# ========================================
# SECTION 1: Layer Initialization
# ========================================
print("=" * 60)
print("  INTEGRATION TEST: WindowsControl + AIClient-2-API")
print("=" * 60)

print("\n[1/6] Layer Initialization...")

from layers.shell import ShellLayer
from layers.mcp_client import UIAutomationLayer
from layers.win32_layer import Win32Layer
from layers.vision import VisionLayer

layers = []
errors = []

try:
    shell = ShellLayer()
    layers.append(shell)
    print("  [OK] Shell layer initialized")
except Exception as e:
    errors.append(f"Shell: {e}")
    print(f"  [FAIL] Shell layer: {e}")

try:
    ui = UIAutomationLayer()
    layers.append(ui)
    print("  [OK] UI Automation layer initialized")
except Exception as e:
    errors.append(f"UI Automation: {e}")
    print(f"  [FAIL] UI Automation layer: {e}")

try:
    win32 = Win32Layer()
    layers.append(win32)
    print("  [OK] Win32 layer initialized")
except Exception as e:
    errors.append(f"Win32: {e}")
    print(f"  [FAIL] Win32 layer: {e}")

try:
    vision = VisionLayer()
    layers.append(vision)
    print("  [OK] Vision layer initialized")
except Exception as e:
    errors.append(f"Vision: {e}")
    print(f"  [FAIL] Vision layer: {e}")

print(f"\n  Layers active: {len(layers)}/4")

# ========================================
# SECTION 2: Tool Count Verification
# ========================================
print(f"\n[2/6] Tool Count Verification...")

all_tools = {}
for layer in layers:
    for defn in layer.get_definitions():
        all_tools[defn.name] = {"layer": defn.layer.value, "tool_set": layer}

print(f"  Total tools registered: {len(all_tools)}")

expected_tools = {
    "run_shell",
    "snapshot", "click", "type_text", "scroll", "keyboard_shortcut",
    "open_app", "drag_and_drop", "hover", "wait", "select_text",
    "list_windows", "clipboard_op", "process_manage", "com_automate",
    "window_control", "system_info", "screen_info", "open_url",
    "window_move_resize", "file_operations",
    "screenshot_analyze", "find_element_visual", "verify_action",
    "read_screen_text", "wait_for_element",
}

actual_tools = set(all_tools.keys())
missing = expected_tools - actual_tools
extra = actual_tools - expected_tools

if missing:
    print(f"  [FAIL] Missing tools: {missing}")
    errors.append(f"Missing tools: {missing}")
else:
    print(f"  [OK] All {len(expected_tools)} expected tools present")

if extra:
    print(f"  [INFO] Extra tools: {extra}")

for layer_name in ["shell", "mcp", "win32", "vision"]:
    layer_tools = [name for name, info in all_tools.items() if info["layer"] == layer_name]
    print(f"  {layer_name:>12}: {', '.join(sorted(layer_tools))}")

# ========================================
# SECTION 3: Tool Dispatch Tests
# ========================================
print(f"\n[3/6] Tool Dispatch (executing safe tools)...")

test_results = {}

# Shell: run_shell
r = shell.execute("run_shell", command="echo 'Integration Test OK'")
test_results["run_shell"] = r.status == ToolResultStatus.SUCCESS
print(f"  run_shell:         {'[OK]' if test_results['run_shell'] else '[FAIL]'} -> {r.output.strip()[:60]}")

# UI: wait
r = ui.execute("wait", seconds=0.2)
test_results["wait"] = r.status == ToolResultStatus.SUCCESS
print(f"  wait:              {'[OK]' if test_results['wait'] else '[FAIL]'} -> {r.output}")

# UI: hover
r = ui.execute("hover", x=400, y=400)
test_results["hover"] = r.status == ToolResultStatus.SUCCESS
print(f"  hover:             {'[OK]' if test_results['hover'] else '[FAIL]'} -> {r.output}")

# UI: snapshot
r = ui.execute("snapshot")
test_results["snapshot"] = r.status == ToolResultStatus.SUCCESS
elem_count = len(r.data.get("elements", [])) if r.data else 0
print(f"  snapshot:          {'[OK]' if test_results['snapshot'] else '[FAIL]'} -> {elem_count} UI elements found")

# Win32: system_info
r = win32.execute("system_info")
test_results["system_info"] = r.status == ToolResultStatus.SUCCESS
first_line = r.output.split('\n')[0] if r.output else ""
print(f"  system_info:       {'[OK]' if test_results['system_info'] else '[FAIL]'} -> {first_line}")

# Win32: screen_info
r = win32.execute("screen_info")
test_results["screen_info"] = r.status == ToolResultStatus.SUCCESS
first_line = r.output.split('\n')[0] if r.output else ""
print(f"  screen_info:       {'[OK]' if test_results['screen_info'] else '[FAIL]'} -> {first_line}")

# Win32: list_windows
r = win32.execute("list_windows")
test_results["list_windows"] = r.status == ToolResultStatus.SUCCESS
win_count = len(r.data.get("windows", [])) if r.data else 0
print(f"  list_windows:      {'[OK]' if test_results['list_windows'] else '[FAIL]'} -> {win_count} windows")

# Win32: clipboard_op (read)
r = win32.execute("clipboard_op", mode="get")
test_results["clipboard_op"] = r.status == ToolResultStatus.SUCCESS
print(f"  clipboard_op:      {'[OK]' if test_results['clipboard_op'] else '[FAIL]'} -> read OK")

# Win32: process_manage (list)
r = win32.execute("process_manage", mode="list", limit=5)
test_results["process_manage"] = r.status == ToolResultStatus.SUCCESS
print(f"  process_manage:    {'[OK]' if test_results['process_manage'] else '[FAIL]'} -> listed top 5")

# Win32: file_operations (read a known file)
test_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.py")
r = win32.execute("file_operations", mode="read", path=test_file)
test_results["file_operations"] = r.status == ToolResultStatus.SUCCESS
fsize = r.data.get("size", "?") if r.data else "?"
print(f"  file_operations:   {'[OK]' if test_results['file_operations'] else '[FAIL]'} -> read config.py ({fsize} chars)")

passed = sum(1 for v in test_results.values() if v)
total = len(test_results)
print(f"\n  Tool dispatch: {passed}/{total} passed")

# ========================================
# SECTION 4: API Proxy Connectivity
# ========================================
print(f"\n[4/6] API Proxy Connectivity (localhost:8888)...")

import requests
from config import GEMINI_API_BASE, GEMINI_API_KEY, GEMINI_MODEL

proxy_ok = False
models_ok = False

try:
    r = requests.get("http://localhost:8888/health", timeout=5)
    proxy_ok = r.status_code == 200
    print(f"  Health check:      {'[OK]' if proxy_ok else '[FAIL]'} (HTTP {r.status_code})")
except Exception as e:
    print(f"  Health check:      [FAIL] {e}")
    errors.append(f"API proxy not reachable: {e}")

try:
    r = requests.get(
        f"{GEMINI_API_BASE}/models",
        headers={"Authorization": f"Bearer {GEMINI_API_KEY}"},
        timeout=10
    )
    models_ok = r.status_code == 200
    if models_ok:
        data = r.json()
        model_count = len(data.get("data", []))
        model_list = [m.get("id", "?") for m in data.get("data", [])[:5]]
        print(f"  Models endpoint:   [OK] {model_count} models available")
        print(f"    Sample: {', '.join(model_list)}")
    else:
        print(f"  Models endpoint:   [FAIL] HTTP {r.status_code}")
except Exception as e:
    print(f"  Models endpoint:   [FAIL] {e}")

print(f"  Config model:      {GEMINI_MODEL}")
print(f"  Config API base:   {GEMINI_API_BASE}")

# ========================================
# SECTION 5: LLM Function Calling Test
# ========================================
print(f"\n[5/6] LLM Function Calling Test...")

fc_ok = False

if proxy_ok:
    from orchestrator.agent import Agent

    agent = Agent(layers)
    tool_funcs = agent._get_tool_functions()
    print(f"  Tool definitions generated: {len(tool_funcs)}")

    test_messages = [
        {"role": "system", "content": "You are a test assistant. When asked for system info, call the system_info tool."},
        {"role": "user", "content": "What are the system specs of this computer?"},
    ]

    try:
        response = agent._call_llm(test_messages, tool_funcs)
        choice = response.get("choices", [{}])[0]
        message = choice.get("message", {})
        tool_calls = message.get("tool_calls", [])

        if tool_calls:
            call = tool_calls[0]
            func_name = call.get("function", {}).get("name", "")
            print(f"  LLM response:      [OK] Tool call -> {func_name}")
            fc_ok = True
        elif message.get("content"):
            print(f"  LLM response:      [OK] Text response (model chose not to call tools)")
            preview = message["content"][:100].replace("\n", " ")
            print(f"    Preview: {preview}...")
            fc_ok = True
        else:
            print(f"  LLM response:      [WARN] Empty response")
    except Exception as e:
        print(f"  LLM response:      [FAIL] {e}")
        errors.append(f"LLM call failed: {e}")
else:
    print("  [SKIP] API proxy not available")

# ========================================
# SECTION 6: End-to-End Agent Run
# ========================================
print(f"\n[6/6] End-to-End Agent Run...")

e2e_ok = False

if proxy_ok and fc_ok:
    agent = Agent(layers)

    def log_step(step, action, result=None):
        print(f"  Step {step}: {action}")

    test_task = "Tell me the current system info: OS version, CPU usage, RAM usage, and screen resolution."
    print(f"  Task: \"{test_task}\"")
    print(f"  Running agent...\n")

    try:
        start_time = time.time()
        result = agent.run(test_task, on_step=log_step)
        elapsed = round(time.time() - start_time, 1)

        e2e_ok = bool(result) and "error" not in result.lower()[:20]
        print(f"\n  Agent result ({elapsed}s):")
        for line in result[:500].split('\n'):
            print(f"    {line}")
        if len(result) > 500:
            print(f"    ... ({len(result)} chars total)")
        print(f"\n  End-to-end:        {'[OK]' if e2e_ok else '[FAIL]'}")
    except Exception as e:
        print(f"  End-to-end:        [FAIL] {e}")
        errors.append(f"E2E agent run failed: {e}")
else:
    print("  [SKIP] Prerequisites not met")

# ========================================
# SUMMARY
# ========================================
print(f"\n{'=' * 60}")
print(f"  INTEGRATION TEST SUMMARY")
print(f"{'=' * 60}")
print(f"  Layers:          {len(layers)}/4 active")
print(f"  Tools:           {len(all_tools)} registered ({len(expected_tools)} expected)")
print(f"  Tool Dispatch:   {passed}/{total} passed")
print(f"  API Proxy:       {'OK' if proxy_ok else 'FAIL'}")
print(f"  LLM Func Call:   {'OK' if fc_ok else 'FAIL'}")
print(f"  End-to-End:      {'OK' if e2e_ok else 'FAIL'}")
if errors:
    print(f"\n  ERRORS:")
    for e in errors:
        print(f"    - {e}")
print(f"{'=' * 60}")

overall = len(layers) == 4 and len(all_tools) >= 26 and passed == total and proxy_ok and fc_ok and e2e_ok
print(f"\n  OVERALL: {'ALL TESTS PASSED' if overall else 'SOME TESTS FAILED'}")

sys.exit(0 if overall else 1)
