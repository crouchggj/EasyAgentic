import os
import subprocess
import json
from pathlib import Path
from typing import Callable, Dict, List, Any
from dataclasses import dataclass, field
from openai import OpenAI

# Configuration
OPENAI_URL = os.environ.get("OPENAI_URL", "https://api.openai.com/v1")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL = os.environ.get("MODEL", "gpt-4o")
WORKDIR = Path(os.environ.get("WORKDIR", ".")).resolve()
TODO_REMINDER_THRESHOLD = 3


# ============================================================================
# Todo Manager
# ============================================================================

class TodoManager:
    """Manage todo items for task tracking."""

    def __init__(self):
        self.items = []

    def update(self, items: list) -> str:
        validated, in_progress_count = [], 0
        for item in items:
            status = item.get("status", "pending")
            if status == "in_progress":
                in_progress_count += 1
            validated.append({
                "id": item["id"],
                "text": item["text"],
                "status": status
            })
        if in_progress_count > 1:
            raise ValueError("Only one task can be in_progress")
        self.items = validated
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "No tasks"
        lines = ["Current tasks:"]
        for item in self.items:
            status_icon = {"pending": "○", "in_progress": "●", "completed": "✓"}
            icon = status_icon.get(item["status"], "○")
            lines.append(f"  {icon} [{item['id']}] {item['text']} ({item['status']})")
        return "\n".join(lines)


# ============================================================================
# Tool Registry
# ============================================================================

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


# ============================================================================
# Tool Handlers
# ============================================================================

def safe_path(p: str) -> Path:
    """Validate and resolve a path within the workspace."""
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def handle_bash(args: dict) -> str:
    """Execute a bash command."""
    command = args["command"]
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=120
        )
        output = result.stdout
        if result.stderr:
            output += f"\nSTDERR: {result.stderr}"
        if result.returncode != 0:
            output += f"\nExit code: {result.returncode}"
        return output.strip() if output.strip() else "Command executed successfully (no output)"
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 120 seconds"
    except Exception as e:
        return f"Error executing command: {str(e)}"


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


def create_todo_handler(todo_manager: TodoManager) -> Callable[[dict], str]:
    """Create todo handler with todo_manager reference."""
    def handle_todo(args: dict) -> str:
        try:
            return todo_manager.update(args["items"])
        except ValueError as e:
            return f"Error: {str(e)}"
    return handle_todo


# ============================================================================
# Tool Definitions
# ============================================================================

BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Execute a bash command and return the output",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The bash command to execute"}
            },
            "required": ["command"]
        }
    }
}

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

TODO_TOOL = {
    "type": "function",
    "function": {
        "name": "todo",
        "description": "Update and manage todo list for task tracking",
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "List of todo items",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "text": {"type": "string"},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}
                        },
                        "required": ["id", "text"]
                    }
                }
            },
            "required": ["items"]
        }
    }
}


# ============================================================================
# Agent Loop
# ============================================================================

SYSTEM = """You are a helpful AI assistant with access to bash commands, file reading, and todo management.
You can execute shell commands, read files, and track tasks with the todo tool.
Always be careful when running commands and explain what you're doing.
Use the todo tool to track progress on multi-step tasks."""


class AgentLoop:
    """Agent loop with decoupled tool execution."""

    def __init__(self, client: OpenAI, registry: ToolRegistry, todo_manager: TodoManager):
        self.client = client
        self.registry = registry
        self.todo_manager = todo_manager
        self.todo_skip_count = 0

    def run(self, query: str) -> str:
        """Run the agent loop for a query."""
        self.todo_skip_count = 0
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": query}
        ]

        while True:
            response = self.client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=self.registry.get_definitions(),
                max_tokens=8000,
            )

            message = response.choices[0].message
            messages.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": message.tool_calls
            })

            if not message.tool_calls:
                return message.content

            # Todo reminder check
            todo_called = any(tc.function.name == "todo" for tc in message.tool_calls)
            if todo_called:
                self.todo_skip_count = 0
            else:
                self.todo_skip_count += 1
                if self.todo_skip_count >= TODO_REMINDER_THRESHOLD and self.todo_manager.items:
                    reminder = f"\n[REMINDER] You have active tasks but haven't updated todo for {self.todo_skip_count} rounds.\n{self.todo_manager.render()}\nConsider calling todo to track progress."
                    messages.append({"role": "user", "content": reminder})
                    self.todo_skip_count = 0
                    continue

            # Execute all tool calls
            for tool_call in message.tool_calls:
                args = json.loads(tool_call.function.arguments)
                output = self.registry.execute(tool_call.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": output,
                })


# ============================================================================
# Main Entry
# ============================================================================

def setup_registry(todo_manager: TodoManager) -> ToolRegistry:
    """Setup and return tool registry."""
    registry = ToolRegistry()
    registry.register("bash", BASH_TOOL, handle_bash)
    registry.register("read_file", READ_FILE_TOOL, handle_read_file)
    registry.register("todo", TODO_TOOL, create_todo_handler(todo_manager))
    return registry


def main():
    """Main entry point."""
    client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_URL)
    todo_manager = TodoManager()
    registry = setup_registry(todo_manager)
    agent = AgentLoop(client, registry, todo_manager)

    print("Simple Agent - Type 'quit' to exit")
    print("-" * 40)

    while True:
        try:
            query = input("\nYou: ").strip()
            if not query:
                continue
            if query.lower() in ["quit", "exit", "q"]:
                print("Goodbye!")
                break

            print("\nAgent: ", end="")
            result = agent.run(query)
            if result:
                print(result)
        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")


if __name__ == "__main__":
    main()