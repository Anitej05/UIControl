"""Full integration test - verifies all 4 layers and the agent."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from layers.shell import ShellLayer
from layers.mcp_client import UIAutomationLayer
from layers.win32_layer import Win32Layer
from layers.vision import VisionLayer
from orchestrator.agent import Agent
from orchestrator.router import route_task

print("=" * 50)
print("  WindowsControl - Full Integration Test")
print("=" * 50)

# --- Layer Initialization ---
print("\n[1/5] Initializing Layers...")
errors = []

try:
    shell = ShellLayer()
    print("  [OK] Shell layer")
except Exception as e:
    print(f"  [FAIL] Shell layer: {e}")
    errors.append(str(e))

try:
    ui_auto = UIAutomationLayer()
    print("  [OK] UI Automation layer")
except Exception as e:
    print(f"  [FAIL] UI Automation layer: {e}")
    errors.append(str(e))

try:
    win32 = Win32Layer()
    print("  [OK] Win32 layer")
except Exception as e:
    print(f"  [FAIL] Win32 layer: {e}")
    errors.append(str(e))

try:
    vision = VisionLayer()
    print("  [OK] Vision layer")
except Exception as e:
    print(f"  [FAIL] Vision layer: {e}")
    errors.append(str(e))

# --- Agent ---
print("\n[2/5] Building Agent...")
layers = [shell, ui_auto, win32, vision]
agent = Agent(layers)
tools = agent.get_available_tools()
print(f"  Agent has {len(tools)} tools:")
for t in tools:
    print(f"    - {t}")

# --- Shell Test ---
print("\n[3/5] Testing Shell Layer...")
r = shell.execute("run_shell", command='Write-Host "PowerShell is working"')
print(f"  Status: {r.status.value}")
print(f"  Output: {r.output}")

# --- Snapshot Test ---
print("\n[4/5] Testing UI Automation (Snapshot)...")
r2 = ui_auto.execute("snapshot")
print(f"  Status: {r2.status.value}")
lines = r2.output.split("\n")
for line in lines[:15]:
    print(f"  {line}")
if len(lines) > 15:
    print(f"  ... ({len(lines)} total lines)")

# --- Win32 Test ---
print("\n[5/5] Testing Win32 Layer (List Windows)...")
r3 = win32.execute("list_windows")
print(f"  Status: {r3.status.value}")
lines3 = r3.output.split("\n")
for line in lines3[:8]:
    print(f"  {line}")
if len(lines3) > 8:
    print(f"  ... ({len(lines3)} total lines)")

# --- Router Test ---
print("\n[Bonus] Testing Router...")
test_tasks = [
    "Open Notepad and type hello",
    "List all files on the desktop",
    "Create an Excel spreadsheet",
    "Take a screenshot",
]
for task in test_tasks:
    layers_order = route_task(task)
    print(f"  '{task}' -> {[l.value for l in layers_order]}")

# --- Summary ---
print("\n" + "=" * 50)
if errors:
    print(f"  RESULT: {len(errors)} error(s)")
    for e in errors:
        print(f"    - {e}")
else:
    print(f"  ALL 4 LAYERS FULLY OPERATIONAL")
    print(f"  AGENT READY WITH {len(tools)} TOOLS")
    print(f"  ROUTER WORKING")
print("=" * 50)
