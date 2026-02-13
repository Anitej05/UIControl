"""
5-Task Test Suite — Simple to Complex
Runs each task through the agent and captures full output.
"""

import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from layers.shell import ShellLayer
from layers.mcp_client import UIAutomationLayer
from layers.win32_layer import Win32Layer
from layers.vision import VisionLayer
from orchestrator.agent import Agent

# Define 5 tasks from simple to complex
TASKS = [
    {
        "id": 1,
        "name": "Open Notepad",
        "difficulty": "Simple",
        "task": "Open Notepad",
        "expected": "Notepad window should appear",
    },
    {
        "id": 2,
        "name": "Type in Notepad",
        "difficulty": "Simple-Medium",
        "task": "Type 'Hello from WindowsControl Agent!' in the Notepad window that is already open",
        "expected": "Text should appear in Notepad",
    },
    {
        "id": 3,
        "name": "YouTube Search",
        "difficulty": "Medium",
        "task": "Open YouTube in the browser and search for 'Claude Opus 4.6'",
        "expected": "YouTube search results for Claude Opus 4.6 should appear",
    },
    {
        "id": 4,
        "name": "Get System Info",
        "difficulty": "Medium-Complex",
        "task": "Tell me my CPU name, RAM size, and the current time",
        "expected": "Agent should report system specs and time",
    },
    {
        "id": 5,
        "name": "Multi-Window Workflow",
        "difficulty": "Complex",
        "task": "Close the Notepad window and then switch back to the browser window that has YouTube open",
        "expected": "Notepad closes, browser comes to foreground",
    },
    {
        "id": 6,
        "name": "Robustness & Verification",
        "difficulty": "Complex",
        "task": "Test parameter aliasing, fuzzy window matching, and auto-verification.",
        "expected": "Notepad snaps left, Chrome snaps right despite bad title/param naming.",
    },
]


def step_callback(step_num, action, result):
    """Print each step as it happens."""
    prefix = f"  Step {step_num}: {action}"
    if result and result.status.value == "error":
        prefix += f"\n    {result.error or result.output}"
    print(prefix)


def run_test(agent, task_info):
    """Run a single test task."""
    print(f"\n{'='*70}")
    print(f"TEST {task_info['id']}: {task_info['name']} [{task_info['difficulty']}]")
    print(f"Task: {task_info['task']}")
    print(f"Expected: {task_info['expected']}")
    print(f"{'='*70}")
    
    start = time.time()
    try:
        if task_info["id"] == 6:
            # Custom runner loop to inject specific bad params for testing
            # Step 1: Open Notepad
            print("  Step 1: Open Notepad")
            res = agent._execute_tool("open_app", {"name": "Notepad"})
            print(f"  Step 1: {res.status.value} -> {res.output[:50]}...")
            time.sleep(1)

            # Step 2: Type text
            print("  Step 2: Type text")
            res = agent._execute_tool("type_text", {"text": "Robustness Test", "x": 500, "y": 500}) # coords don't matter for notepad usually if focused
            print(f"  Step 2: {res.status.value} -> {res.output[:50]}...")
            
            # Step 3: Snap with BAD PARAM (side instead of position)
            print("  Step 3: Snap Notepad (side='left')")
            # Direct tool call to bypass LLM correction, testing execute() remapping
            res = agent._execute_tool("snap_window", {"title": "Notepad", "side": "left"})
            print(f"  Step 3: {res.status.value} -> {res.output}")

            # Step 4: Snap with BAD TITLE (Longer than actual)
            # Ensure Chrome is open first
            agent._execute_tool("open_url", {"url": "youtube.com"})
            time.sleep(2)
            print("  Step 4: Snap Chrome (title='YouTube - Google Chrome - Extra Garbage')")
            res = agent._execute_tool("snap_window", {"title": "YouTube - Google Chrome - Extra Garbage", "position": "right"})
            print(f"  Step 4: {res.status.value} -> {res.output}")
            result = "Custom test steps executed." # Placeholder result for custom flow
        else:
            result = agent.run(task_info["task"], on_step=step_callback)
        
        elapsed = time.time() - start
        print(f"\n{'─'*40}")
        print(f"RESULT ({elapsed:.1f}s):")
        print(str(result)[:500]) # Ensure result is string for slicing
        print(f"{'─'*40}")
        return {"id": task_info["id"], "status": "completed", "time": elapsed, "result": str(result)[:200]}
    except Exception as e:
        elapsed = time.time() - start
        print(f"\n❌ EXCEPTION ({elapsed:.1f}s): {str(e)}")
        return {"id": task_info["id"], "status": "exception", "time": elapsed, "error": str(e)}


def main():
    print("Initializing layers...")
    shell = ShellLayer()
    ui_auto = UIAutomationLayer()
    win32 = Win32Layer()
    vision = VisionLayer()
    agent = Agent([shell, ui_auto, win32, vision])
    print(f"✓ Agent ready with {len(agent.get_available_tools())} tools")

    # Check which test to run
    if len(sys.argv) > 1:
        test_id = int(sys.argv[1])
        tasks = [t for t in TASKS if t["id"] == test_id]
        if not tasks:
            print(f"Unknown test ID: {test_id}. Valid: 1-5")
            return
    else:
        tasks = TASKS

    results = []
    for task_info in tasks:
        result = run_test(agent, task_info)
        results.append(result)
        # Wait between tests to let UI settle
        if task_info != tasks[-1]:
            print("\n⏳ Waiting 3s before next test...")
            time.sleep(3)

    # Summary
    print(f"\n\n{'='*70}")
    print("TEST SUMMARY")
    print(f"{'='*70}")
    for r in results:
        status_icon = "✅" if r["status"] == "completed" else "❌"
        print(f"  {status_icon} Test {r['id']}: {r['status']} ({r['time']:.1f}s)")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
