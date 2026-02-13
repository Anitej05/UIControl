"""Debug script to examine snapshot output and understand coordinate issues."""
import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from layers.mcp_client import UIAutomationLayer

ui = UIAutomationLayer()
r = ui.execute('snapshot')

print("=== SNAPSHOT OUTPUT (what LLM sees) ===")
print(r.output[:3000])
print("...")

elements = r.data.get("elements", []) if r.data else []
print(f"\nTotal elements: {len(elements)}")
print("\n=== FIRST 15 ELEMENTS ===")
for i, e in enumerate(elements[:15]):
    print(f"  [{i}] {e}")

print("\n=== SCREEN INFO ===")
if r.data:
    for k, v in r.data.items():
        if k != "elements":
            print(f"  {k}: {v}")

# Also test what the ToolResult.__str__ looks like (what gets sent to LLM)
print("\n=== ToolResult str (first 1500 chars) ===")
result_str = str(r)
print(result_str[:1500])
