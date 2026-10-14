"""Compatibility exports; concrete tools and agents are imported only on demand."""
from importlib import import_module

_EXPORTS = {
    'Tool': 'tools.base',
    'ToolRegistry': 'tools.base',
    'BASH_TOOL': 'tools.bash',
    'handle_bash': 'tools.bash',
    'WRITE_FILE_TOOL': 'tools.write_file',
    'handle_write_file': 'tools.write_file',
    'READ_FILE_TOOL': 'tools.read_file',
    'handle_read_file': 'tools.read_file',
    'TODO_TOOL': 'tools.todo',
    'TodoManager': 'tools.todo',
    'create_todo_handler': 'tools.todo',
    'TASK_TOOL': 'tools.task',
    'create_task_handler': 'tools.task',
    'SubAgent': 'easyagentic.agents.subagent',
    'SUBAGENT_SYSTEM': 'easyagentic.agents.subagent',
    'setup_registry': 'tools.registry',
    'COLORS': 'tools.colors',
    'colorize': 'tools.colors',
    'round_header': 'tools.colors',
    'SkillLoader': 'tools.skills',
    'Skill': 'tools.skills',
    'SKILL_TOOL': 'tools.skills',
    'create_skill_handler': 'tools.skills',
    'MessageBus': 'agent_runtime.messages',
    'TeammateManager': 'easyagentic.agents.teammates',
    'Teammate': 'easyagentic.agents.teammates',
    'TEAMMATE_SYSTEM': 'easyagentic.agents.teammates',
    'SPAWN_TOOL': 'tools.collaboration',
    'SEND_TOOL': 'tools.collaboration',
    'READ_INBOX_TOOL': 'tools.collaboration',
    'create_spawn_handler': 'tools.collaboration',
    'create_send_handler': 'tools.collaboration',
    'create_read_inbox_handler': 'tools.collaboration',
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
