
import sys, os, time
import win32com.client
import win32gui
import win32process
import psutil

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from layers.shell import ShellLayer
from layers.mcp_client import UIAutomationLayer
from layers.win32_layer import Win32Layer
from orchestrator.agent import Agent

def kill_word():
    """Kill any existing WINWORD.EXE processes."""
    for proc in psutil.process_iter(['name']):
        if proc.info['name'] and 'winword' in proc.info['name'].lower():
            try:
                proc.kill()
            except:
                pass

def test_word_complex():
    print("=" * 60)
    print("  Complex Word Automation Test: Tables & Lists")
    print("=" * 60)

    # Initialize Layers
    try:
        shell = ShellLayer()
        ui_auto = UIAutomationLayer()
        win32 = Win32Layer()
        print("[Step 1] Layers Initialized.")
    except Exception as e:
        print(f"[FAIL] Layer init failed: {e}")
        return

    # Cleanup
    print("\n[Step 2] Cleaning up/Preparing...")
    kill_word()
    time.sleep(1)

    # Launch Word
    print("\n[Step 3] Launching Word (Clean Instance)...")
    # /q = no splash, /n = new instance, /w = blank doc
    cmd = 'Start-Process "winword" -ArgumentList "/q", "/n", "/w"' 
    shell.execute("run_shell", command=cmd)
    
    # Wait for Window
    print("Waiting for Word window...")
    hwnd = None
    for _ in range(10):
        # Look for typical Word window titles
        # Usually "Document1 - Word" or just "Word" depending on version
        hwnd = win32._find_window_by_title("Word")
        if hwnd:
            break
        time.sleep(1)
            
    if not hwnd:
        print("[FAIL] Word window not found.")
        return
        
    print(f"[OK] Word window found (HWND: {hwnd})")
    win32.execute("set_active_window", title="Word")
    time.sleep(1)

    # --- Table Creation ---
    print("\n[Step 4] Creating Table (UI Automation)...")
    # Using Word's auto-format: +---+---+ (standard ASCII table trick)
    # Type: +-------+-------+-------+ [Enter]
    table_pattern = "+-------+-------+-------+" 
    
    print(f"Typing pattern: {table_pattern}")
    ui_auto.execute("type_text", text=table_pattern)
    time.sleep(0.5)
    ui_auto.execute("keyboard_shortcut", shortcut="enter")
    time.sleep(1) # Allow auto-format to trigger

    # Fill Table
    print("Populating Table...")
    # Row 1 (Headers)
    ui_auto.execute("type_text", text="Item")
    ui_auto.execute("keyboard_shortcut", shortcut="tab")
    ui_auto.execute("type_text", text="Qty")
    ui_auto.execute("keyboard_shortcut", shortcut="tab")
    ui_auto.execute("type_text", text="Price")
    ui_auto.execute("keyboard_shortcut", shortcut="tab") # Moves to next row

    # Row 2 (Data)
    ui_auto.execute("type_text", text="Apple")
    ui_auto.execute("keyboard_shortcut", shortcut="tab")
    ui_auto.execute("type_text", text="5")
    ui_auto.execute("keyboard_shortcut", shortcut="tab")
    ui_auto.execute("type_text", text="$1.00")
    
    # Exit Table
    print("Exiting Table...")
    # Down arrow twice usually exits table
    ui_auto.execute("keyboard_shortcut", shortcut="down")
    time.sleep(0.2)
    ui_auto.execute("keyboard_shortcut", shortcut="down")
    time.sleep(0.2)
    ui_auto.execute("keyboard_shortcut", shortcut="enter") # New paragraph

    # --- Bullet List Creation ---
    print("\n[Step 5] Creating Bullet List (UI Automation)...")
    ui_auto.execute("type_text", text="Shopping List:")
    ui_auto.execute("keyboard_shortcut", shortcut="enter")
    
    # Trigger Bullet AutoFormat: "* "
    ui_auto.execute("type_text", text="* ") 
    # Usually space after * triggers it, but sometimes need Enter. 
    # 'type_text' types literally. 
    # Let's see if Word picks i tup.
    
    ui_auto.execute("type_text", text="Milk")
    ui_auto.execute("keyboard_shortcut", shortcut="enter") # Should continue list
    
    ui_auto.execute("type_text", text="Bread")
    ui_auto.execute("keyboard_shortcut", shortcut="enter")
    
    ui_auto.execute("type_text", text="Cheese")
    ui_auto.execute("keyboard_shortcut", shortcut="enter")

    # Stop List (Enter twice)
    ui_auto.execute("keyboard_shortcut", shortcut="enter") 

    time.sleep(2) # Wait for actions to settle

    # --- Verification via COM ---
    print("\n[Step 6] Verifying State via COM (Internal Object Model)...")
    try:
        # Get active Word instance
        word = win32com.client.GetActiveObject("Word.Application")
        doc = word.ActiveDocument
        
        # Verify 1 Table
        table_count = doc.Tables.Count
        if table_count == 1:
            print(f"  [PASS] Table Count: {table_count}")
        else:
            print(f"  [FAIL] Table Count: {table_count} (Expected 1)")

        # Verify List Paragraphs
        # Word lists are paragraphs with ListFormat.ListType != 0
        list_items = 0
        for p in doc.Paragraphs:
            if p.Range.ListFormat.ListType != 0: # wdListNoNumbering (0)
                list_items += 1
        
        # We expect at least 3 items (Milk, Bread, Cheese)
        # Note: Auto-format might create a list object.
        if list_items >= 3:
             print(f"  [PASS] List Items Detected: {list_items}")
        else:
             print(f"  [WARN] List Items Detected: {list_items} (Expected >= 3). Auto-format might have failed.")
             
        # Verify Content
        content = doc.Content.Text
        print(f"  [INFO] Document Length: {len(content)} chars")
        if "Apple" in content and "Milk" in content:
            print("  [PASS] Content Verification: Found 'Apple' (Table) and 'Milk' (List)")
        else:
            print("  [FAIL] Content Verification: Missing keywords.")

    except Exception as e:
        print(f"  [FAIL] COM Verification Error: {e}")

    # --- Teardown ---
    print("\n[Step 7] Cleaning Up...")
    try:
        doc.Close(SaveChanges=False) # wdDoNotSaveChanges = 0
        word.Quit()
        print("  [OK] Word Closed.")
    except:
        print("  [WARN] Could not close Word cleanly. Killing process.")
        kill_word()

    print("=" * 60)
    print("  Test Complete")
    print("=" * 60)

if __name__ == "__main__":
    test_word_complex()
