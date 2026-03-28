"""Tool base classes and registry."""

from typing import Callable, Dict, List
from dataclasses import dataclass


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
        return self._tools[name].handler(args)

    def has_tool(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools