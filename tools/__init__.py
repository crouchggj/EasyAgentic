"""Tools package."""

from .base import Tool, ToolRegistry
from .bash import BASH_TOOL, handle_bash
from .read_file import READ_FILE_TOOL, handle_read_file
from .todo import TODO_TOOL, TodoManager, create_todo_handler


def setup_registry(todo_manager: TodoManager) -> ToolRegistry:
    """Setup and return tool registry with all tools registered."""
    registry = ToolRegistry()
    registry.register("bash", BASH_TOOL, handle_bash)
    registry.register("read_file", READ_FILE_TOOL, handle_read_file)
    registry.register("todo", TODO_TOOL, create_todo_handler(todo_manager))
    return registry


__all__ = [
    "Tool",
    "ToolRegistry",
    "BASH_TOOL",
    "handle_bash",
    "READ_FILE_TOOL",
    "handle_read_file",
    "TODO_TOOL",
    "TodoManager",
    "create_todo_handler",
    "setup_registry",
]