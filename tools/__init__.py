"""Tools package."""

from .base import Tool, ToolRegistry
from .bash import BASH_TOOL, handle_bash
from .read_file import READ_FILE_TOOL, handle_read_file
from .todo import TODO_TOOL, TodoManager, create_todo_handler
from .task import TASK_TOOL, create_task_handler
from .subagent import SubAgent, SUBAGENT_SYSTEM
from .colors import COLORS, TOOL_COLORS, colorize, tool_header, tool_args, tool_output, round_header
from .skills import SkillLoader, Skill, SKILL_TOOL, create_skill_handler


def setup_registry(todo_manager: TodoManager, subagent_runner=None, skill_loader: SkillLoader = None) -> ToolRegistry:
    """Setup and return tool registry with all tools registered.

    Args:
        todo_manager: TodoManager instance
        subagent_runner: Optional callable for running subagents. If provided,
                         the task tool will be registered.
        skill_loader: Optional SkillLoader instance. If provided,
                      the skill tool will be registered.
    """
    registry = ToolRegistry()
    registry.register("bash", BASH_TOOL, handle_bash)
    registry.register("read_file", READ_FILE_TOOL, handle_read_file)
    registry.register("todo", TODO_TOOL, create_todo_handler(todo_manager))
    if subagent_runner:
        registry.register("task", TASK_TOOL, create_task_handler(subagent_runner))
    if skill_loader:
        registry.register("skill", SKILL_TOOL, create_skill_handler(skill_loader))
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
    "TASK_TOOL",
    "create_task_handler",
    "SubAgent",
    "SUBAGENT_SYSTEM",
    "setup_registry",
    "COLORS",
    "TOOL_COLORS",
    "colorize",
    "tool_header",
    "tool_args",
    "tool_output",
    "round_header",
    "SkillLoader",
    "Skill",
    "SKILL_TOOL",
    "create_skill_handler",
]