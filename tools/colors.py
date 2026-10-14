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

def colorize(text: str, color: str, styles: list = None) -> str:
    """Apply color and optional styles to text."""
    color_code = COLORS.get(color, COLORS["blue"])
    style_codes = "".join(COLORS.get(s, "") for s in (styles or []))
    return f"{color_code}{style_codes}{text}{COLORS['reset']}"


def round_header(round_num: int) -> str:
    """Format round number header."""
    return f"\n{COLORS['bright_blue']}{COLORS['bold']}━━━ Round {round_num} ━━━{COLORS['reset']}"
