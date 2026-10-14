"""Compatibility facade; orchestration lives in easyagentic.agents.teammates."""
from agent_runtime.messages import MessageBus
from easyagentic.agents.teammates import TeammateManager, Teammate, TEAMMATE_SYSTEM
from .collaboration import (
    SPAWN_TOOL, SEND_TOOL, READ_INBOX_TOOL,
    create_spawn_handler, create_send_handler, create_read_inbox_handler,
)

__all__ = [
    "MessageBus", "TeammateManager", "Teammate", "TEAMMATE_SYSTEM",
    "SPAWN_TOOL", "SEND_TOOL", "READ_INBOX_TOOL",
    "create_spawn_handler", "create_send_handler", "create_read_inbox_handler",
]
