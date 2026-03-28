"""Read file tool implementation."""

import os
from pathlib import Path

WORKDIR = Path(os.environ.get("WORKDIR", ".")).resolve()

READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a file from the workspace and return its contents",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The relative path to the file"},
                "limit": {"type": "integer", "description": "Optional limit on number of lines"}
            },
            "required": ["path"]
        }
    }
}


def safe_path(p: str) -> Path:
    """Validate and resolve a path within the workspace."""
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def handle_read_file(args: dict) -> str:
    """Read a file from the workspace."""
    path = args["path"]
    limit = args.get("limit")
    try:
        text = safe_path(path).read_text()
        lines = text.splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit]
        return "\n".join(lines)[:50000]
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except Exception as e:
        return f"Error reading file: {str(e)}"