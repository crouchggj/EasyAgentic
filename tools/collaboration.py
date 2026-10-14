"""Model-facing collaboration schemas and thin manager/message adapters."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from agent_runtime.messages import MessageBus
    from easyagentic.agents.teammates import TeammateManager


SPAWN_TOOL = {
    "type": "function",
    "function": {
        "name": "spawn",
        "description": "Spawn a new teammate agent with a specific role. The teammate will run persistently and can communicate via messages.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Unique name for the teammate"},
                "role": {"type": "string", "description": "Role description (e.g., 'coder', 'tester', 'reviewer')"},
                "prompt": {"type": "string", "description": "Initial instructions for the teammate"}
            },
            "required": ["name", "role", "prompt"]
        }
    }
}

SEND_TOOL = {
    "type": "function",
    "function": {
        "name": "send",
        "description": "Send a message to a teammate's inbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Name of the recipient teammate"},
                "content": {"type": "string", "description": "Message content"},
                "message_id": {"type": "string", "description": "Optional stable ID for idempotent retries"}
            },
            "required": ["to", "content"]
        }
    }
}

READ_INBOX_TOOL = {
    "type": "function",
    "function": {
        "name": "read_inbox",
        "description": "Read and clear your inbox. Returns all pending messages.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
}


def create_spawn_handler(manager: TeammateManager) -> Callable:
    """Create spawn tool handler."""
    def handle_spawn(args: dict) -> str:
        return manager.spawn(args["name"], args["role"], args["prompt"])
    return handle_spawn


def create_send_handler(bus: MessageBus, sender: str) -> Callable:
    """Create send tool handler."""
    def handle_send(args: dict) -> str:
        return bus.send(sender, args["to"], args["content"], message_id=args.get("message_id"))
    return handle_send


def create_read_inbox_handler(bus: MessageBus, name: str, registry=None) -> Callable:
    """Create read_inbox tool handler."""
    def handle_read_inbox(args: dict) -> str:
        if registry is None:
            return bus.read_inbox(name)
        deliveries = bus.claim(name)
        registry.pending_messages.append((bus, deliveries))
        return json.dumps([d["payload"] for d in deliveries if d["id"] not in registry.seen_messages])
    return handle_read_inbox
