
import sys, os, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from layers.shell import ShellLayer
from layers.mcp_client import UIAutomationLayer
from layers.win32_layer import Win32Layer
from layers.vision import VisionLayer
from orchestrator.agent import Agent

def test_stateful_integration():
    print("=" * 60)
    print("  Stateful Integration Test: Notepad Workflow")
    print("=" * 60)
    print("\n[Step 1] Initializing Layers...")

    try:
        shell = ShellLayer()
        ui_auto = UIAutomationLayer()
        win32 = Win32Layer()
        vision = VisionLayer() # Optional but good to have
        print("  [OK] Layers Initialized.")
    except Exception as e:
        print(f"  [FAIL] Initialization: {e}")
        return

    # --- Setup ---
    print("\n[Step 2] Establishing Initial State via Shell...")
    # Create a clean file
    filename = "stateful_test_document.txt"
    filepath = os.path.abspath(filename)
    
    # 1. Clean up potential leftovers
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
            print(f"  [OK] Cleaned up existing {filename}")
        except Exception as e:
            print(f"  [WARN] Cleanup failed: {e}")

    # 2. Create file with initial content
    cmd_create = f'Set-Content -Path "{filepath}" -Value "Initial Content"'
    res_create = shell.execute("run_shell", command=cmd_create)
    if res_create.status != "success":
        print(f"  [FAIL] Failed to create file: {res_create.output}")
        return
    print(f"  [OK] Created file: {filename} with content 'Initial Content'")

    # --- Interaction ---
    print("\n[Step 3] Modifying External State via UI (Notepad)...")
    
    # 3. Open Notepad
    cmd_open = f'Start-Process "notepad.exe" -ArgumentList "{filepath}"'
    res_open = shell.execute("run_shell", command=cmd_open)
    if res_open.status != "success":
        print(f"  [FAIL] Failed to launch Notepad: {res_open.output}")
        return
    print("  [OK] Launched Notepad.")
    time.sleep(2) # Allow launch

    # 4. Verify Notepad Window Exists (Win32 Layer)
    # Using partial title match just in case "stateful_test_document" is truncated or formatted differently
    window_title = "stateful_test_document"
    res_verify = win32.execute("execute_list_windows") # Oops, mapped internally
    # Wait, let's use list_windows or get_active_window
    
    # Let's try to focus it first to be sure
    print("  [Action] Finding Notepad window...")
    found_notepad = False
    for _ in range(5):
        res_list = win32.execute("list_windows")
        if window_title in res_list.output:
            found_notepad = True
            print(f"  [OK] Found window containing '{window_title}'")
            break
        time.sleep(1)
    
    if not found_notepad:
        print(f"  [FAIL] Notepad window not found. Output:\n{res_list.output}")
        # Proceeding might fail, but let's try
    
    # 5. Type Text (UI Layer)
    # Move to end of file (Ctrl+End just in case) and add a new line
    print("  [Action] Typing new content...")
    
    # First, make sure it's focused
    win32.execute("set_active_window", title=window_title)
    time.sleep(0.5)

    # Use Ctrl+End to go to end of "Initial Content"
    ui_auto.execute("keyboard_shortcut", shortcut="ctrl+end")
    time.sleep(0.1)
    
    # Type newline + " - Appended by Agent"
    ui_auto.execute("keyboard_shortcut", shortcut="enter")
    time.sleep(0.1)
    
    ui_auto.execute("type_text", text="- Appended by Agent", x=0, y=0) 
    # Note: x,y 0,0 usually ignores coordinates if using `type_text` generic logic, 
    # but the implementation might require valid coords if it clicks first.
    # Let's just blindly type for now, relying on focus.
    
    # 6. Save (UI Layer)
    print("  [Action] Saving changes (Ctrl+S)...")
    ui_auto.execute("keyboard_shortcut", shortcut="ctrl+s")
    time.sleep(1) # Wait for save I/O

    # --- Cleanup & Verification ---
    print("\n[Step 4] Verifying System State Change...")
    
    # 7. Close Notepad (Win32 Layer)
    print("  [Action] Closing Notepad...")
    win32.execute("window_control", title=window_title, action="close")
    time.sleep(1)
    
    # 8. Check File Content (Shell Layer)
    print("  [Action] Reading file content...")
    cmd_read = f'Get-Content -Path "{filepath}" -Raw'
    res_read = shell.execute("run_shell", command=cmd_read)
    
    content = res_read.output.strip()
    print(f"  [Result] Content: '{content}'")
    
    expected = "Initial Content\r\n- Appended by Agent" # Notepad on Windows usually adds CRLF
    # Normalize for comparison
    content_norm = content.replace("\r\n", "\n").strip()
    expected_norm = "Initial Content\n- Appended by Agent"
    
    if "- Appended by Agent" in content:
         print("  [PASS] State verification successful! Content was modified.")
    else:
         print(f"  [FAIL] Content mismatch.\nExpected to contain: '- Appended by Agent'\nActual: {content}")

    # --- Final Cleanup ---
    print("\n[Step 5] Final System Cleanup...")
    try:
        os.remove(filepath)
        print(f"  [OK] Deleted test file {filename}")
    except Exception as e:
        print(f"  [WARN] Function failed: {e}")

    print("=" * 60)
    print("  Test Complete")
    print("=" * 60)

if __name__ == "__main__":
    test_stateful_integration()
