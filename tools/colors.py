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
    "default": COLORS["blue"],
}


def colorize(text: str, color: str) -> str:
    """Apply color to text."""
    color_code = COLORS.get(color, COLORS["blue"])
    return f"{color_code}{text}{COLORS['reset']}"


def tool_header(tool_name: str) -> str:
    """Format tool name with color header."""
    color = TOOL_COLORS.get(tool_name, TOOL_COLORS["default"])
    return f"\n{color}{COLORS['bold']}▸ {tool_name}{COLORS['reset']}"


def tool_args(args: dict, indent: int = 2) -> str:
    """Format tool arguments with dimmed color."""
    lines = []
    indent_str = " " * indent
    for key, value in args.items():
        # Truncate long values
        value_str = str(value)
        if len(value_str) > 100:
            value_str = value_str[:100] + "..."
        lines.append(f"{indent_str}{COLORS['dim']}{key}:{COLORS['reset']} {value_str}")
    return "\n".join(lines)


def tool_output(output: str, indent: int = 2, max_lines: int = 5) -> str:
    """Format tool output with dimmed color, truncated."""
    lines = output.split("\n")
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} more lines)"]

    indent_str = " " * indent
    formatted_lines = [f"{indent_str}{COLORS['dim']}{line}{COLORS['reset']}" for line in lines]
    return "\n".join(formatted_lines)


def print_tool_call(tool_name: str, args: dict, output: str = None):
    """Print a formatted tool call with color."""
    print(tool_header(tool_name))
    print(tool_args(args))
    if output:
        print(tool_output(output))


def round_header(round_num: int) -> str:
    """Format round number header."""
    return f"\n{COLORS['bright_blue']}{COLORS['bold']}━━━ Round {round_num} ━━━{COLORS['reset']}"