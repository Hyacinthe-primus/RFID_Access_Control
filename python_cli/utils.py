"""
utils.py
Terminal output helpers. Uses rich lib if installed for nicer tables/colors,
falls back to plain print() so the CLI still works with zero extra deps.
"""

from typing import List

try:
    from rich.console import Console
    from rich.table import Table
    console = Console()
    _HAVE_RICH = True
except ImportError:
    _HAVE_RICH = False


def info(message: str) -> None:
    if _HAVE_RICH:
        console.print(f"[cyan]i[/cyan] {message}")
    else:
        print(f"i {message}")


def success(message: str) -> None:
    if _HAVE_RICH:
        console.print(f"[green]OK[/green] {message}")
    else:
        print(f"+ {message}")


def error(message: str) -> None:
    if _HAVE_RICH:
        console.print(f"[bold red]ERROR[/bold red] {message}")
    else:
        print(f"x {message}")


def warning(message: str) -> None:
    if _HAVE_RICH:
        console.print(f"[yellow]WARN[/yellow] {message}")
    else:
        print(f"! {message}")


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
        console.print(table)

        bar_width = 40
        filled = int(bar_width * used_pct / 100)
        bar_color = "green" if used_pct < 75 else ("yellow" if used_pct < 90 else "red")
        bar = (
            f"[{bar_color}]" + "█" * filled + "[/]"
            + "[grey37]" + "░" * (bar_width - filled) + "[/grey37]"
        )
        console.print(bar)
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

    from database import expiration_date_str

    if _HAVE_RICH:
        table = Table(title=f"Registered Users ({len(users)})")
        table.add_column("UID", style="magenta")
        table.add_column("Name", style="white")
        table.add_column("Registered", style="white")
        table.add_column("Valid Days", style="white")
        table.add_column("Expires (approx.)", style="yellow")
        for u in users:
            # Visual distinction for admin badges: highlight the name and
            # show "ADMIN" instead of an expiration date.
            name_cell = f"[bold yellow](ADMIN)[/bold yellow] {u.name}" if u.is_admin else u.name
            registered_cell = "-" if u.is_admin else u.registered
            valid_days_cell = "-" if u.is_admin else str(u.valid_days)
            table.add_row(u.uid, name_cell, registered_cell, valid_days_cell, expiration_date_str(u))
        console.print(table)
    else:
        print(f"Registered Users ({len(users)})")
        print("-" * 40)
        for u in users:
            if u.is_admin:
                print(
                    f"{u.uid:<20} {u.name:<20} [ADMIN] (no expiry)"
                )
            else:
                print(
                    f"{u.uid:<20} {u.name:<20} registered={u.registered} "
                    f"valid_days={u.valid_days} expires~={expiration_date_str(u)}"
                )


def _rssi_bars(rssi: int) -> str:
    """Crude 4-bar signal indicator for display next to the RSSI number."""
    if rssi == 0:
        return ""
    # Typical Wi-Fi RSSI range: -100 (worst) to -30 (excellent).
    if rssi >= -55:
        return "████"
    if rssi >= -65:
        return "███░"
    if rssi >= -75:
        return "██░░"
    if rssi >= -85:
        return "█░░░"
    return "░░░░"


def print_net_status(status) -> None:
    if _HAVE_RICH:
        table = Table(title="Network Status")
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="white")

        if status.connected:
            bars = _rssi_bars(status.rssi)
            table.add_row("Wi-Fi", "[green]Connected[/green]")
            table.add_row("SSID", status.ssid or "(unknown)")
            table.add_row("IP Address", status.ip or "(unknown)")
            table.add_row("Signal (RSSI)", f"{status.rssi} dBm  {bars}")
        else:
            table.add_row("Wi-Fi", "[red]Not connected[/red]")
            table.add_row("SSID", "(none)")
            table.add_row("IP Address", "(none)")

        time_cell = "[green]Synced[/green]" if status.time_synced else "[red]Not synced[/red]"
        table.add_row("NTP time", time_cell)
        console.print(table)
    else:
        print("Network Status")
        print("-" * 40)
        if status.connected:
            print(f"Wi-Fi       : Connected")
            print(f"SSID        : {status.ssid or '(unknown)'}")
            print(f"IP Address  : {status.ip or '(unknown)'}")
            print(f"Signal      : {status.rssi} dBm  {_rssi_bars(status.rssi)}")
        else:
            print("Wi-Fi       : Not connected")
            print("SSID        : (none)")
            print("IP Address  : (none)")
        print(f"NTP time    : {'Synced' if status.time_synced else 'Not synced'}")

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
        console.print(table)
    else:
        print(f"Serial Ports ({len(ports)})")
        print("-" * 60)
        for p in ports:
            print(f"{p['device']:<10} {_format_id(p['vid']):<8} {_format_id(p['pid']):<8} {p['description']}")