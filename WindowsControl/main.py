"""
WindowsControl — Hybrid AI-Powered Windows Control System
Main entry point with Rich CLI interface.

Supports two operating modes:
  • Execute Mode  — gesture-driven control via webcam hand tracking
  • AutoPilot Mode — AI agent autonomously executes tasks via LLM
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from prompt_toolkit import prompt

from layers.shell import ShellLayer
from layers.mcp_client import UIAutomationLayer
from layers.win32_layer import Win32Layer
from layers.vision import VisionLayer
from mode_manager import ExecutionMode, AutoPilotMode

console = Console()

BANNER = """
╦ ╦╦╔╗╔╔╦╗╔═╗╦ ╦╔═╗  ╔═╗╔═╗╔╗╔╔╦╗╦═╗╔═╗╦  
║║║║║║║ ║║║ ║║║║║╚═╗  ║  ║ ║║║║ ║ ╠╦╝║ ║║  
╚╩╝╩╝╚╝═╩╝╚═╝╚╩╝╚═╝  ╚═╝╚═╝╝╚╝ ╩ ╩╚═╚═╝╩═╝
        Hybrid AI-Powered Desktop Control
"""


def create_layers() -> list:
    """Initialize all tool layers."""
    console.print("[dim]Initializing layers...[/dim]")

    layers = []

    # Layer 1: Shell (always available)
    try:
        shell = ShellLayer()
        layers.append(shell)
        console.print("  [green]✓[/green] Shell layer (PowerShell)")
    except Exception as e:
        console.print(f"  [red]✗[/red] Shell layer: {e}")

    # Layer 2: UI Automation (native — comtypes UIAutomation + PyAutoGUI)
    try:
        ui_auto = UIAutomationLayer()
        layers.append(ui_auto)
        console.print("  [green]✓[/green] UI Automation layer (a11y tree + PyAutoGUI)")
    except Exception as e:
        console.print(f"  [yellow]⚠[/yellow] UI Automation layer: {e}")

    # Layer 3: Win32
    try:
        win32 = Win32Layer()
        layers.append(win32)
        console.print("  [green]✓[/green] Win32 layer (pywin32)")
    except Exception as e:
        console.print(f"  [red]✗[/red] Win32 layer: {e}")

    # Layer 4: Vision
    try:
        vision = VisionLayer()
        layers.append(vision)
        console.print("  [green]✓[/green] Vision layer (Gemini)")
    except Exception as e:
        console.print(f"  [yellow]⚠[/yellow] Vision layer: {e}")

    if not layers:
        console.print("[red]No layers available. Cannot start.[/red]")
        sys.exit(1)

    return layers


def show_mode_menu() -> str:
    """Display the mode selection menu and return the choice."""
    console.print()
    console.print(Panel(
        "[bold]Select Operating Mode:[/bold]\n\n"
        "  [cyan][1][/cyan]  🤚  [bold cyan]Execute Mode[/bold cyan]\n"
        "       Control the desktop with hand gestures.\n"
        "       Requires: webcam + gesture server running on port 8000.\n\n"
        "  [magenta][2][/magenta]  🤖  [bold magenta]AutoPilot Mode[/bold magenta]\n"
        "       AI agent autonomously executes your typed tasks.\n"
        "       Powered by Gemini LLM.\n\n"
        "  [red][q][/red]     Exit",
        title="Mode Selection",
        border_style="bright_blue",
        expand=False,
    ))

    while True:
        try:
            choice = prompt("Select mode (1/2/q): ").strip().lower()
            if choice in ("1", "execute", "e"):
                return "execute"
            elif choice in ("2", "autopilot", "a"):
                return "autopilot"
            elif choice in ("q", "quit", "exit"):
                return "quit"
            else:
                console.print("[dim]Please enter 1, 2, or q.[/dim]")
        except (KeyboardInterrupt, EOFError):
            return "quit"


def main():
    console.print(Panel(BANNER, border_style="bright_blue", expand=False))
    console.print("[dim]Powered by Gemini 3 Flash via Antigravity Proxy (localhost:8888)[/dim]\n")

    # Initialize layers once — shared between both modes
    layers = create_layers()

    while True:
        mode = show_mode_menu()

        if mode == "quit":
            console.print("[dim]Goodbye![/dim]")
            break
        elif mode == "execute":
            exec_mode = ExecutionMode(layers)
            exec_mode.start()
        elif mode == "autopilot":
            auto_mode = AutoPilotMode(layers)
            auto_mode.start()

    console.print("[dim]Session ended.[/dim]")


if __name__ == "__main__":
    main()
