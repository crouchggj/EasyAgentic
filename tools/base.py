"""Tool base classes and registry."""

import json
from typing import Callable, Dict, List
from dataclasses import dataclass
from agent_runtime.execution import TaskStopped
from agent_runtime.review import ReviewRequired


@dataclass
class Tool:
    """Represents a tool with definition and handler."""
    name: str
    definition: dict
    handler: Callable[[dict], str]


class ToolRegistry:
    """Registry for managing tools."""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}
        self.seen_messages = set()
        self.pending_messages = []

    def register(self, name: str, definition: dict, handler: Callable[[dict], str]):
        """Register a tool with its definition and handler."""
        self._tools[name] = Tool(name=name, definition=definition, handler=handler)

    def get_definitions(self) -> List[dict]:
        """Get all tool definitions for API."""
        return [tool.definition for tool in self._tools.values()]

    def execute(self, name: str, args: dict) -> str:
        """Execute a tool by name with given arguments."""
        if name not in self._tools:
            return f"Error: Unknown tool '{name}'"
        try:
            if not isinstance(args, dict):
                raise ValueError("Tool arguments must be a JSON object")
            result = self._tools[name].handler(args)
            if not isinstance(result, str):
                raise TypeError("Tool handlers must return strings")
            return result
        except (TaskStopped, ReviewRequired):
            raise
        except Exception as exc:
            return f"Error executing tool '{name}': {exc}"

    def execute_json(self, name: str, arguments: str) -> str:
        """Return a tool result even when model arguments are malformed."""
        try:
            args = json.loads(arguments)
        except (ValueError, TypeError) as exc:
            return f"Error: Invalid JSON arguments for '{name}': {exc}"
        return self.execute(name, args)

    def received_ids(self):
        return self.seen_messages | {d['id'] for _, deliveries in self.pending_messages for d in deliveries}

    def after_result(self):
        # The caller has durably saved this tool result and the corresponding IDs.
        pending, self.pending_messages = self.pending_messages, []
        for bus, deliveries in pending:
            self.seen_messages.update(d['id'] for d in deliveries)
            bus.acknowledge(deliveries)

    def has_tool(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools
