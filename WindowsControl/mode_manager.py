"""
Mode Manager — Orchestrates Execute Mode (gesture-driven) vs AutoPilot Mode (AI-driven).
Both modes share the same BaseTool layer instances.
"""

import asyncio
import logging
import sys
import os
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text
from prompt_toolkit import prompt
from prompt_toolkit.history import FileHistory

from tools.base import BaseTool
from gesture_handler import GestureHandler
from ws_client import GestureWSClient
from orchestrator.agent import Agent
from config import GESTURE_WS_URL, SPEECH_WS_URL
import tts

log = logging.getLogger("mode_manager")
console = Console()


# ═════════════════════════════════════════════════════════════════════════════
#  Execute Mode — Gesture-Driven Control
# ═════════════════════════════════════════════════════════════════════════════

class ExecutionMode:
    """
    Gesture-based control mode.
    Connects to the gesture/speech WebSocket server and translates
    real-time hand gestures into desktop control tool calls.
    """

    def __init__(self, tool_sets: list[BaseTool]):
        self._tool_sets = tool_sets
        self._handler = GestureHandler(tool_sets)
        self._client = GestureWSClient(
            gesture_url=GESTURE_WS_URL,
            speech_url=SPEECH_WS_URL,
        )

    def start(self):
        """Start the execution mode event loop (blocks until quit)."""
        console.print(Panel(
            "[bold cyan]EXECUTE MODE[/bold cyan] — Gesture Control Active\n\n"
            "Your hand gestures are now controlling the desktop.\n\n"
            "[dim]Gesture Mapping:[/dim]\n"
            "  • [yellow]Tap[/yellow]          → Left Click\n"
            "  • [yellow]Double Tap[/yellow]    → Double Click\n"
            "  • [yellow]Pinch Hold[/yellow]    → Right Click\n"
            "  • [yellow]Pinch Drag[/yellow]    → Drag & Drop\n"
            "  • [yellow]Pinch Flick[/yellow]   → Scroll\n\n"
            f"[dim]Gesture server: {GESTURE_WS_URL}[/dim]\n"
            f"[dim]Speech server:  {SPEECH_WS_URL}[/dim]\n\n"
            "[bold red]Press Ctrl+C to stop and return to mode selection.[/bold red]",
            title="🤚 Execute Mode",
            border_style="cyan",
            expand=False,
        ))

        try:
            asyncio.run(self._run_async())
        except KeyboardInterrupt:
            console.print("\n[dim]Execute Mode stopped.[/dim]")

    async def _run_async(self):
        """Async entry point — runs the WS client event loop."""
        try:
            await self._client.run(
                on_gesture=self._on_gesture,
                on_speech=self._on_speech,
            )
        except asyncio.CancelledError:
            pass
        finally:
            await self._client.stop()

    def _on_gesture(self, event: dict):
        """Callback for gesture events from the WebSocket."""
        # Always track cursor position (moves the OS mouse to follow the hand)
        self._handler.handle_cursor(event)

        # Then check for gesture actions (tap, drag, etc.)
        result = self._handler.handle_event(event)
        if result is not None:
            gtype = event.get("gesture", {}).get("type", "?")
            if result.status.value == "error":
                console.print(f"  [red]✗ {gtype}:[/red] {result.error}")
            else:
                console.print(f"  [green]✓ {gtype}:[/green] {result.output[:120]}")

    def _on_speech(self, event: dict):
        """Callback for speech events (transcript display only for now)."""
        text = self._handler.handle_speech(event)
        if text:
            console.print(f"  [blue]🎤 Speech:[/blue] \"{text}\"")


# ═════════════════════════════════════════════════════════════════════════════
#  AutoPilot Mode — AI-Driven Control (text + speech input)
# ═════════════════════════════════════════════════════════════════════════════

class AutoPilotMode:
    """
    AI-driven control mode.
    Offers two input methods:
      1. Text — traditional REPL (user types tasks)
      2. Speech — receives voice commands via the speech WebSocket
    Both pipe tasks through Agent.run() and speak the result via Kokoro TTS.
    """

    def __init__(self, tool_sets: list[BaseTool]):
        self._agent = Agent(tool_sets)

    def start(self):
        """Start the AutoPilot mode with input selection."""
        tools = self._agent.get_available_tools()
        console.print(f"\n[dim]Available tools: {', '.join(tools)}[/dim]")

        # ── Input Mode Selection ────────────────────────────────────
        console.print(Panel(
            "[bold magenta]AUTOPILOT MODE[/bold magenta] — AI Agent Active\n\n"
            "Select input method:\n\n"
            "  [bold yellow][1][/bold yellow]  ⌨️   [bold]Text Input[/bold]\n"
            "        Type tasks in a REPL prompt.\n\n"
            "  [bold yellow][2][/bold yellow]  🎤  [bold]Speech Input[/bold]\n"
            "        Speak tasks — uses the speech server for recognition.\n"
            "        Requires: speech client + server running on port 8000.\n\n"
            "  [dim]Both modes speak results aloud via Kokoro TTS.[/dim]",
            title="🤖 AutoPilot Mode",
            border_style="magenta",
            expand=False,
        ))

        while True:
            try:
                choice = input("Input mode (1=text, 2=speech, q=back): ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                return

            if choice in ("q", "quit", "back"):
                return
            elif choice == "1":
                self._run_text_mode()
                return
            elif choice == "2":
                self._run_speech_mode()
                return
            else:
                print("Please enter 1, 2, or q.")

    # ── Text Input Mode ─────────────────────────────────────────────────

    def _run_text_mode(self):
        """Text-based REPL for AutoPilot."""
        console.print(Panel(
            "⌨️  [bold]Text Input Mode[/bold]\n\n"
            "Type a task and the AI will execute it.\n"
            "Results will be spoken aloud via Kokoro TTS.\n\n"
            "[dim]Commands:[/dim]\n"
            "  • Type any task to execute\n"
            "  • [yellow]tools[/yellow]  — list available tools\n"
            "  • [yellow]help[/yellow]   — show help\n"
            "  • [yellow]quit[/yellow]   — return to mode selection",
            title="Text Input",
            border_style="blue",
            expand=False,
        ))

        console.print("\n[bold green]Ready![/bold green] Type your task or 'quit' to go back.\n")

        history_path = os.path.join(os.path.dirname(__file__), ".command_history")
        history = FileHistory(history_path)

        while True:
            try:
                user_input = prompt(
                    "🤖  > ",
                    history=history,
                ).strip()

                if not user_input:
                    continue

                if user_input.lower() in ("quit", "exit", "q", "back"):
                    console.print("[dim]Returning to mode selection...[/dim]")
                    break

                if user_input.lower() == "tools":
                    tools = self._agent.get_available_tools()
                    console.print(f"[bold]Available tools:[/bold] {', '.join(tools)}")
                    continue

                if user_input.lower() == "help":
                    self._show_help()
                    continue

                self._execute_task(user_input)

            except KeyboardInterrupt:
                console.print("\n[dim]Interrupted. Type 'quit' to go back.[/dim]")
                continue
            except EOFError:
                break

    # ── Speech Input Mode ───────────────────────────────────────────────

    def _run_speech_mode(self):
        """Speech-based input for AutoPilot via WebSocket."""
        console.print(Panel(
            "🎤  [bold]Speech Input Mode[/bold]\n\n"
            "Speak your tasks — the speech server will recognise them\n"
            "and the AI agent will execute them automatically.\n\n"
            "Results will be spoken aloud via Kokoro TTS.\n\n"
            f"[dim]Speech server: {SPEECH_WS_URL}[/dim]\n\n"
            "[bold red]Press Ctrl+C to stop and return to mode selection.[/bold red]",
            title="Speech Input",
            border_style="green",
            expand=False,
        ))

        tts.speak_async("Speech input mode activated. I'm listening.")

        try:
            asyncio.run(self._speech_loop())
        except KeyboardInterrupt:
            console.print("\n[dim]Speech mode stopped.[/dim]")

    async def _speech_loop(self):
        """Async loop listening for speech transcripts on the WebSocket."""
        import json
        import websockets

        while True:
            try:
                log.info("Connecting to speech server at %s …", SPEECH_WS_URL)
                console.print(f"[dim]Connecting to {SPEECH_WS_URL}…[/dim]")

                async with websockets.connect(
                    SPEECH_WS_URL,
                    max_size=2**20,
                    ping_interval=20,
                    ping_timeout=10,
                ) as ws:
                    console.print("[bold green]🎤 Connected! Start speaking…[/bold green]\n")

                    async for raw in ws:
                        try:
                            if isinstance(raw, bytes):
                                event = json.loads(raw.decode("utf-8"))
                            else:
                                event = json.loads(raw)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue

                        # Extract final transcripts
                        speech = event.get("speech", {})
                        if speech.get("type") != "transcript" or speech.get("state") != "final":
                            # Show listening/speaking status
                            if speech.get("type") == "status":
                                state = speech.get("state", "")
                                if state == "speaking":
                                    console.print("[dim yellow]🎙  Listening to you…[/dim yellow]")
                            continue

                        text = speech.get("data", {}).get("text", "").strip()
                        if not text:
                            continue

                        confidence = speech.get("data", {}).get("confidence", 0.0)
                        console.print(
                            f"\n[bold blue]🎤 You said:[/bold blue] \"{text}\" "
                            f"[dim](conf={confidence:.2f})[/dim]"
                        )

                        # Execute in a thread so the WS stays alive
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(None, self._execute_task, text)

                        console.print("\n[bold green]🎤 Listening for next command…[/bold green]")

            except websockets.exceptions.ConnectionClosed:
                console.print("[yellow]Speech connection lost. Reconnecting…[/yellow]")
                await asyncio.sleep(2.0)
            except OSError as e:
                console.print(f"[yellow]Speech connection failed ({e}). Retrying in 3s…[/yellow]")
                await asyncio.sleep(3.0)

    # ── Shared task execution ───────────────────────────────────────────

    def _execute_task(self, task: str):
        """Run a task through the agent and speak the result."""
        console.print(f"\n[bold]Task:[/bold] {task}")
        console.print("[dim]Working...[/dim]\n")

        try:
            result = self._agent.run(task, on_step=self._on_step)
            console.print(f"\n{Panel(Markdown(result), title='Result', border_style='green')}\n")

            # Speak the result aloud
            # Strip markdown for cleaner speech — take first ~500 chars
            clean_text = result.replace("**", "").replace("*", "").replace("#", "")
            clean_text = clean_text.replace("`", "").replace("\n", ". ").strip()
            if clean_text:
                tts.speak_async(clean_text)

        except Exception as e:
            error_msg = f"An error occurred: {str(e)}"
            console.print(f"\n[red]{error_msg}[/red]\n")
            tts.speak_async(error_msg)

    def _show_help(self):
        console.print(Panel(
            "• Type any task to execute it\n"
            "• 'tools' — list available tools\n"
            "• 'quit' — return to mode selection\n\n"
            "Examples:\n"
            "  Open Notepad and type Hello World\n"
            "  What applications are running?\n"
            "  Take a screenshot and describe what you see\n"
            "  List all files on the desktop\n"
            "  Change system to dark mode",
            title="Help",
            border_style="blue",
        ))

    @staticmethod
    def _on_step(step: int, action: str, result=None):
        """Callback for agent execution steps."""
        if result and result.status.value == "error":
            console.print(f"  [red]Step {step}:[/red] {action}")
            if result.error:
                console.print(f"    [dim red]{result.error[:200]}[/dim red]")
        else:
            console.print(f"  [cyan]Step {step}:[/cyan] {action}")
