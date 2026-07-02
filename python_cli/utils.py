"""
utils.py
Terminal output helpers. Uses `rich` if installed for nicer tables/colors,
falls back to plain print() so the CLI still works with zero extra deps.
"""

from typing import List

try:
    from rich.console import Console
    from rich.table import Table
    _console = Console()
    _HAVE_RICH = True
except ImportError:
    _HAVE_RICH = False


def info(message: str) -> None:
    if _HAVE_RICH:
        _console.print(f"[cyan]INFO[/cyan]  {message}")
    else:
        print(f"INFO  {message}")


def success(message: str) -> None:
    if _HAVE_RICH:
        _console.print(f"[green]OK[/green]    {message}")
    else:
        print(f"OK    {message}")


def error(message: str) -> None:
    if _HAVE_RICH:
        _console.print(f"[bold red]ERROR[/bold red] {message}")
    else:
        print(f"ERROR {message}")


def _human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def print_status(status) -> None:
    total = status.fs_total_bytes
    used_pct = (status.fs_used_bytes / total * 100) if total else 0.0

    if _HAVE_RICH:
        table = Table(title="Device Status")
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="white")
        table.add_row("Database file", status.db_path)
        table.add_row("Registered users", str(status.user_count))
        table.add_row(
            "Storage used",
            f"{_human_bytes(status.fs_used_bytes)} / {_human_bytes(total)} "
            f"({used_pct:.1f}%)",
        )
        table.add_row("Storage free", _human_bytes(status.fs_free_bytes))
        _console.print(table)

        bar_width = 40
        filled = int(bar_width * used_pct / 100)
        bar_color = "green" if used_pct < 75 else ("yellow" if used_pct < 90 else "red")
        bar = (
            f"[{bar_color}]" + "█" * filled + "[/]"
            + "[grey37]" + "░" * (bar_width - filled) + "[/grey37]"
        )
        _console.print(bar)
    else:
        print("Device Status")
        print("-" * 40)
        print(f"Database file : {status.db_path}")
        print(f"Users         : {status.user_count}")
        print(
            f"Storage       : {_human_bytes(status.fs_used_bytes)} / "
            f"{_human_bytes(total)} ({used_pct:.1f}%)"
        )
        print(f"Free          : {_human_bytes(status.fs_free_bytes)}")


def print_user_table(users: List) -> None:
    if not users:
        info("No users registered.")
        return

    if _HAVE_RICH:
        table = Table(title=f"Registered Users ({len(users)})")
        table.add_column("UID", style="magenta")
        table.add_column("Name", style="white")
        for u in users:
            table.add_row(u.uid, u.name)
        _console.print(table)
    else:
        print(f"Registered Users ({len(users)})")
        print("-" * 40)
        for u in users:
            print(f"{u.uid:<20} {u.name}")

def _format_id(n) -> str:
    return f"0x{n:04X}" if isinstance(n, int) else "-"

def print_port_table(ports: List) -> None:
    if not ports:
        info("No serial ports detected.")
        return

    if _HAVE_RICH:
        table = Table(title=f"Serial Ports ({len(ports)})")
        table.add_column("Device", style="magenta")
        table.add_column("VID", style="yellow")
        table.add_column("PID", style="yellow")
        table.add_column("Manufacturer", style="white")
        table.add_column("Description", style="white")
        for p in ports:
            table.add_row(
                p["device"], _format_id(p["vid"]), _format_id(p["pid"]),
                p["manufacturer"], p["description"],
            )
        _console.print(table)
    else:
        print(f"Serial Ports ({len(ports)})")
        print("-" * 60)
        for p in ports:
            print(f"{p['device']:<10} {_format_id(p['vid']):<8} {_format_id(p['pid']):<8} {p['description']}")