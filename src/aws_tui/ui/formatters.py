from __future__ import annotations


def humanize_bytes(value: int | None) -> str:
    """Return a compact byte count for transfer UI surfaces."""
    if value is None:
        return "?"
    units = ("B", "kB", "MB", "GB", "TB")
    size = float(value)
    for unit in units:
        # Compare the displayed value: 1023.96 would print as 1024.0 kB otherwise.
        if round(size, 1) < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{value} B"


__all__ = ["humanize_bytes"]
