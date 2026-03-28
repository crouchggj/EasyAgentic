"""Color utilities for terminal output."""

# ANSI color codes
COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",

    # Foreground colors
    "black": "\033[30m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[37m",

    # Bright foreground colors
    "bright_red": "\033[91m",
    "bright_green": "\033[92m",
    "bright_yellow": "\033[93m",
    "bright_blue": "\033[94m",
    "bright_magenta": "\033[95m",
    "bright_cyan": "\033[96m",
}

# Tool-specific colors
TOOL_COLORS = {
    "bash": COLORS["yellow"],
    "read_file": COLORS["cyan"],
    "todo": COLORS["magenta"],
    "task": COLORS["green"],
    "skill": COLORS["bright_magenta"],
    "spawn": COLORS["bright_green"],
    "send": COLORS["bright_cyan"],
    "read_inbox": COLORS["cyan"],
    "default": COLORS["blue"],
}


def colorize(text: str, color: str, styles: list = None) -> str:
    """Apply color and optional styles to text."""
    color_code = COLORS.get(color, COLORS["blue"])
    style_codes = "".join(COLORS.get(s, "") for s in (styles or []))
    return f"{color_code}{style_codes}{text}{COLORS['reset']}"


def tool_header(tool_name: str) -> str:
    """Format tool name with color header."""
    color = TOOL_COLORS.get(tool_name, TOOL_COLORS["default"])
    return f"\n{color}{COLORS['bold']}▸ {tool_name}{COLORS['reset']}"


def tool_args(args: dict, indent: int = 2) -> str:
    """Format tool arguments with dimmed color."""
    if not args:
        return ""

    lines = []
    indent_str = " " * indent
    for key, value in args.items():
        value_str = value if isinstance(value, str) else str(value)
        if len(value_str) > 100:
            value_str = value_str[:100] + "..."
        lines.append(f"{indent_str}{COLORS['dim']}{key}:{COLORS['reset']} {value_str}")
    return "\n".join(lines)


def tool_output(output: str, indent: int = 2, max_lines: int = 5) -> str:
    """Format tool output with dimmed color, truncated."""
    if not output:
        return ""

    indent_str = " " * indent
    lines = []

    # Use split with maxsplit to avoid creating full list for large outputs
    parts = output.split("\n", max_lines + 1)
    for i, line in enumerate(parts[:max_lines]):
        lines.append(f"{indent_str}{COLORS['dim']}{line}{COLORS['reset']}")

    if len(parts) > max_lines:
        lines.append(f"{indent_str}{COLORS['dim']}... (more lines){COLORS['reset']}")

    return "\n".join(lines)


def round_header(round_num: int) -> str:
    """Format round number header."""
    return f"\n{COLORS['bright_blue']}{COLORS['bold']}━━━ Round {round_num} ━━━{COLORS['reset']}"