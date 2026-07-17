#!/usr/bin/env python3
"""
repl.py
Interactive shell for the RFID CLI.

Rich (utils.console) handles all output (banner, tables, status). 
prompt_toolkit is used only for line editing, history, and TAB completion.

Commands are parsed by cli.build_parser() and executed through the same
commands.cmd_* handlers as the one-shot CLI. Connection management remains
centralized in commands._connection(); this module contains no command
parsing or business logic.
"""

import argparse
import os
import platform
import shlex
import time
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout

from rich import box
from rich.align import Align
from rich.panel import Panel
from rich.table import Table

import cli
import protocol
import utils
from database import DatabaseResponseError
from serial_manager import SerialManager, SerialManagerError, find_esp32_ports

if not utils._HAVE_RICH:
    raise ImportError("rich is required for interactive mode")

console = utils.console

VERSION = "1.0.0"
HISTORY_PATH = os.path.expanduser("~/.rfid_cli_history")

ALIASES = {"ls": "list", "rm": "remove", "?": "help"}
META_COMMANDS = {"help", "clear", "exit", "quit", "connect", "disconnect", "reconnect", "version", "history"}
META_HELP = {
    "help": "Show this help",
    "history": "Show command history",
    "version": "Show CLI, firmware, protocol, and Python versions",
    "clear": "Clear the screen",
    "exit": "Exit the shell",
    "quit": "Exit the shell",
    "connect": "Connect to a device on the given port",
    "disconnect": "Disconnect without leaving the shell",
    "reconnect": "Reconnect to the last device",
}

# Presentational grouping only -- cli.command_help() supplies the actual
# text for real subcommands, so descriptions can't drift out of sync with
# --help.
COMMAND_GROUPS = {
    "Database": ["list", "find", "add", "remove", "rename", "import", "export", "sync"],
    "Device": ["status", "scan", "configure", "connect", "disconnect", "reconnect", "netstatus"],
    "Time": ["ntp-sync", "ntp-time", "timezone"],
    "General": ["help", "history", "version", "clear", "exit", "quit"],
}

# Destructive flag combos that get an extra confirmation in the shell (the
# scripted CLI leaves this to the caller, as today).
_DESTRUCTIVE = {
    "remove": ("--force", "This will delete ALL users from the device."),
    "import": ("--clear", "This will wipe existing users before importing."),
}

class _ConnState:
    """Mutable connection state shared by the prompt, completer, and
    command dispatch loop -- lets connect/disconnect/reconnect update all
    three without rebuilding the session."""

    def __init__(self):
        self.sm: Optional[SerialManager] = None
        self.last_port: Optional[str] = None

    @property
    def is_connected(self) -> bool:
        return self.sm is not None

    @property
    def port(self) -> Optional[str]:
        return self.sm.port if self.sm else self.last_port

    def connect(self, port: str) -> bool:
        sm = SerialManager(port=port)
        try:
            sm.open()
        except SerialManagerError as exc:
            utils.error(str(exc))
            return False
        if self.sm is not None:
            self.sm.close()
        self.sm = sm
        self.last_port = port
        return True

    def disconnect(self) -> None:
        if self.sm is not None:
            self.last_port = self.sm.port
            self.sm.close()
            self.sm = None


class _RfidCompleter(Completer):
    """TAB completion: commands/aliases as the first word, filesystem paths
    for import/export. The REPL never talks to the device to build
    completions -- only commands/aliases and local paths are completed."""

    def __init__(self, parser: argparse.ArgumentParser):
        self._commands = sorted(cli.subcommand_names(parser) | META_COMMANDS | set(ALIASES))
        self._path_completer = PathCompleter(expanduser=True)

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        words = text.split(" ")

        if len(words) <= 1:
            word = words[0]
            for name in self._commands:
                if name.startswith(word):
                    yield Completion(name, start_position=-len(word))
            return

        cmd = ALIASES.get(words[0], words[0])
        word = words[-1]

        if cmd in ("import", "export", "sync"):
            sub_doc = Document(word, len(word))
            yield from self._path_completer.get_completions(sub_doc, complete_event)


def _select_port() -> Optional[str]:
    """Startup port selection. Returns a port string to try, or None if the
    user cancelled."""
    candidates = find_esp32_ports()

    if len(candidates) == 1:
        return candidates[0]

    if len(candidates) > 1:
        console.print("Available serial ports:\n")
        for i, port in enumerate(candidates, 1):
            console.print(f"  [{i}] {port}")
        console.print()
        while True:
            choice = input("Select port: ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(candidates):
                return candidates[int(choice) - 1]
            utils.error("Invalid selection.")

    utils.info("No ESP32 detected automatically.")
    while True:
        port = input("\nEnter serial port (COM5, /dev/ttyUSB0, etc), or blank to cancel:\n> ").strip()
        if not port:
            return None
        return port


def _connect_with_retry(initial_port=None) -> _ConnState:
    """Open the initial connection, looping back to manual entry on
    failure. Returns a _ConnState -- disconnected if the user cancelled."""
    state = _ConnState()
    port = initial_port
    while True:
        if port is None:
            port = _select_port()
            if port is None:
                return state
        if state.connect(port):
            return state
        port = None  # fall back to manual entry next loop


def _print_banner(state: _ConnState) -> None:
    body = f"CLI v{VERSION}"
    panel = Panel(Align.center(body), title="RFID Access Control", border_style="cyan", width=52, box=box.DOUBLE)
    console.print(panel)
    if state.is_connected:
        console.print(f"Connected to {state.port} (ESP32-S3)\n")
        _print_welcome_info(state)
    else:
        console.print("Not connected. Use 'connect <port>' to attach a device.\n")
    console.print("Type 'help' for available commands.")
    console.print("Press Ctrl+D or type 'exit' to quit.\n")


def _print_welcome_info(state: _ConnState) -> None:
    """Device/Firmware/Protocol/Serial port summary -- confirms comms are
    working. firmware_version/protocol_version aren't in the current wire
    protocol (see database.DeviceStatus), so they're read defensively and
    shown as 'unknown' rather than guessed."""
    try:
        raw = state.sm.request(protocol.build_status())
    except (SerialManagerError, DatabaseResponseError):
        return
    table = Table.grid(padding=(0, 1))
    table.add_column(style="cyan")
    table.add_column()
    table.add_row("Device:", "ESP32-S3")
    table.add_row("Firmware:", str(raw.get("firmware_version", "unknown")))
    table.add_row("Protocol:", str(raw.get("protocol_version", "unknown")))
    table.add_row("Serial port:", state.port)
    console.print(table)
    console.print()


def _print_help(parser: argparse.ArgumentParser) -> None:
    help_map = cli.command_help(parser)
    for group, cmd_names in COMMAND_GROUPS.items():
        table = Table(title=group, show_header=False, box=None, title_justify="left")
        table.add_column(style="cyan", no_wrap=True)
        table.add_column()
        for name in cmd_names:
            desc = help_map.get(name, META_HELP.get(name, ""))
            table.add_row(name, desc)
        console.print(table)
    if ALIASES:
        alias_list = ", ".join(f"{a} -> {full}" for a, full in ALIASES.items())
        console.print(f"[bold]Aliases[/bold]: {alias_list}\n")


def _print_version(state: _ConnState) -> None:
    firmware = "(not connected)"
    protocol_version = "(not connected)"
    if state.is_connected:
        try:
            raw = state.sm.request(protocol.build_status())
            firmware = str(raw.get("firmware_version", "unknown"))
            protocol_version = str(raw.get("protocol_version", "unknown"))
        except (SerialManagerError, DatabaseResponseError):
            firmware = protocol_version = "unknown"
    console.print(f"RFID CLI {VERSION}")
    console.print(f"Firmware {firmware}")
    console.print(f"Protocol {protocol_version}")
    console.print(f"Python {platform.python_version()}")


def _print_history(session: PromptSession) -> None:
    entries = list(session.history.load_history_strings())
    entries.reverse()  # oldest first
    if not entries:
        utils.info("No history yet.")
        return
    for i, line in enumerate(entries, 1):
        console.print(f"[cyan]{i:>4}[/cyan]  {line}")


def _prompt_text(state: _ConnState) -> HTML:
    label = state.port if state.is_connected else "disconnected"
    return HTML(f"<ansigreen>rfid({label})&gt; </ansigreen>")


def _is_destructive(tokens) -> Optional[str]:
    rule = _DESTRUCTIVE.get(tokens[0])
    if rule and rule[0] in tokens:
        return rule[1]
    return None


def _handle_disconnect(state: _ConnState) -> None:
    """A device command just failed and sm.ping() confirmed the link is
    down. Try once to reconnect on the same port; otherwise drop to
    disconnected state so the shell stays usable."""
    utils.error("Connection lost.")
    utils.info("Attempting reconnection...")
    port = state.port
    state.disconnect()
    if state.connect(port):
        utils.success(f"Reconnected to {state.port}.")
    else:
        utils.error("Device unavailable.")


def run_repl(parser: argparse.ArgumentParser, initial_port=None) -> None:
    state = _connect_with_retry(initial_port)
    _print_banner(state)

    valid_commands = cli.subcommand_names(parser)
    session = PromptSession(history=FileHistory(HISTORY_PATH), completer=_RfidCompleter(parser))

    try:
        while True:
            try:
                line = session.prompt(_prompt_text(state))
            except KeyboardInterrupt:
                continue  # Ctrl+C: cancel current input, fresh prompt
            except EOFError:
                break  # Ctrl+D

            line = line.strip()
            if not line:
                continue

            try:
                tokens = shlex.split(line)
            except ValueError as exc:
                utils.error(f"Could not parse input: {exc}")
                continue
            if not tokens:
                continue

            tokens[0] = ALIASES.get(tokens[0], tokens[0])
            cmd_word = tokens[0]

            if cmd_word in ("exit", "quit"):
                break
            if cmd_word == "help":
                _print_help(parser)
                continue
            if cmd_word == "clear":
                console.clear()
                continue
            if cmd_word == "version":
                _print_version(state)
                continue
            if cmd_word == "history":
                _print_history(session)
                continue
            if cmd_word == "connect":
                if len(tokens) != 2:
                    utils.error("Usage: connect <port>")
                    continue
                if state.is_connected:
                    utils.info(f"Disconnected from {state.port}.")
                    state.disconnect()
                if state.connect(tokens[1]):
                    utils.success(f"Connected to {state.port}.")
                continue
            if cmd_word == "disconnect":
                if not state.is_connected:
                    utils.info("Already disconnected.")
                    continue
                port = state.port
                state.disconnect()
                utils.success(f"Disconnected from {port}.")
                continue
            if cmd_word == "reconnect":
                if state.last_port is None:
                    utils.error("No previous device to reconnect to.")
                    continue
                if state.connect(state.last_port):
                    utils.success(f"Reconnected to {state.port}.")
                continue

            if cmd_word not in valid_commands:
                console.print(f"Unknown command: {tokens[0]}")
                console.print("Type 'help' for available commands.")
                continue

            if cmd_word in cli.DEVICE_COMMANDS and not state.is_connected:
                utils.warning("Not connected. Use 'connect <port>' or 'reconnect'.")
                continue

            warning_text = _is_destructive(tokens)
            if warning_text:
                utils.warning(warning_text)
                if not input("Continue? [y/N] ").strip().lower().startswith("y"):
                    utils.info("Cancelled.")
                    continue

            try:
                args = parser.parse_args(tokens)
            except SystemExit:
                continue  # argparse already printed its own usage/error

            args._sm = state.sm  # commands._connection() reuses this instead of opening a new port

            start = time.monotonic()
            try:
                args.func(args)
            except SystemExit:
                if state.is_connected and not state.sm.ping():
                    _handle_disconnect(state)
                continue
            except KeyboardInterrupt:
                utils.warning("Interrupted.")
                continue
            except Exception as exc:  # a command bug should never kill the shell
                utils.error(f"Unexpected error: {exc}")
                continue

            elapsed = time.monotonic() - start
            if elapsed > 1.0:
                utils.info(f"Elapsed: {elapsed:.2f} s")
    finally:
        state.disconnect()