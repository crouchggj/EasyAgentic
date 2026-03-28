"""Agent Teams - Persistent multi-agent collaboration with message bus."""

import os
import json
import time
import threading
from pathlib import Path
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field
from openai import OpenAI

from .base import ToolRegistry
from .bash import BASH_TOOL, handle_bash
from .read_file import READ_FILE_TOOL, handle_read_file
from .todo import TODO_TOOL, TodoManager, create_todo_handler
from .colors import COLORS, colorize, tool_header, tool_args, tool_output, round_header


# ============================================================================
# MessageBus - Append-only JSONL inbox for inter-agent communication
# ============================================================================

class MessageBus:
    """Message bus for inter-agent communication via JSONL files.

    - send(): append message to recipient's inbox
    - read_inbox(): read and drain inbox (consume-on-read)
    """

    def __init__(self, team_dir: Path):
        self.dir = team_dir / "inbox"
        self.dir.mkdir(parents=True, exist_ok=True)

    def send(self, sender: str, to: str, content: str,
             msg_type: str = "message", extra: dict = None) -> str:
        """Send a message to a teammate's inbox."""
        msg = {
            "type": msg_type,
            "from": sender,
            "content": content,
            "timestamp": time.time()
        }
        if extra:
            msg.update(extra)

        inbox_path = self.dir / f"{to}.jsonl"
        with open(inbox_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        return f"Message sent to {to}"

    def read_inbox(self, name: str) -> str:
        """Read and drain inbox for a teammate."""
        inbox_path = self.dir / f"{name}.jsonl"
        if not inbox_path.exists():
            return "[]"

        lines = inbox_path.read_text(encoding="utf-8").strip().splitlines()
        if not lines or not lines[0]:
            return "[]"

        msgs = [json.loads(line) for line in lines if line.strip()]
        # Drain: clear the inbox after reading
        inbox_path.write_text("", encoding="utf-8")

        return json.dumps(msgs, indent=2, ensure_ascii=False)

    def broadcast(self, sender: str, content: str,
                  msg_type: str = "broadcast", members: List[str] = None) -> str:
        """Broadcast a message to all team members."""
        recipients = members or []
        for name in recipients:
            if name != sender:  # Don't send to self
                self.send(sender, name, content, msg_type)
        return f"Broadcast sent to {len(recipients)} members"


# ============================================================================
# TeammateManager - Manage persistent teammate agents
# ============================================================================

TEAMMATE_SYSTEM = """You are a specialized teammate agent with a specific role.
You have access to bash commands, file reading, and todo management.
Check your inbox regularly for messages from other teammates.
Communicate results and updates by sending messages.
Focus on your assigned role and collaborate with the team."""


@dataclass
class Teammate:
    """Represents a teammate agent."""
    name: str
    role: str
    status: str = "working"  # working, idle, shutdown
    thread: threading.Thread = None
    stop_event: threading.Event = None


class TeammateManager:
    """Manage persistent teammate agents with identity and communication."""

    MAX_ITERATIONS = 50

    def __init__(self, team_dir: Path, client: OpenAI, model: str, bus: MessageBus):
        self.team_dir = team_dir
        self.client = client
        self.model = model
        self.bus = bus

        # Setup config
        self.config_path = team_dir / "config.json"
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config = self._load_config()

        # Track teammates
        self.teammates: Dict[str, Teammate] = {}

    def _load_config(self) -> dict:
        """Load or create team config."""
        if self.config_path.exists():
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        return {"members": []}

    def _save_config(self):
        """Save team config."""
        self.config_path.write_text(
            json.dumps(self.config, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

    def _setup_teammate_registry(self) -> ToolRegistry:
        """Setup registry with teammate tools (bash, read_file, todo)."""
        registry = ToolRegistry()
        registry.register("bash", BASH_TOOL, handle_bash)
        registry.register("read_file", READ_FILE_TOOL, handle_read_file)
        # Each teammate has its own todo manager
        todo_manager = TodoManager()
        registry.register("todo", TODO_TOOL, create_todo_handler(todo_manager))
        return registry

    def _teammate_loop(self, name: str, role: str, prompt: str, stop_event: threading.Event):
        """Main loop for a teammate agent."""
        print(colorize(f"\n[Team] {name} ({role}) started", "green"))

        registry = self._setup_teammate_registry()
        messages = [{"role": "user", "content": prompt}]

        for iteration in range(self.MAX_ITERATIONS):
            if stop_event.is_set():
                break

            # Check inbox before each iteration
            inbox = self.bus.read_inbox(name)
            if inbox != "[]":
                messages.append({
                    "role": "user",
                    "content": f"<inbox>{inbox}</inbox>"
                })
                messages.append({
                    "role": "assistant",
                    "content": "Noted inbox messages."
                })

            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": TEAMMATE_SYSTEM}] + messages,
                    tools=registry.get_definitions(),
                    max_tokens=4000,
                )

                message = response.choices[0].message
                messages.append({
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": message.tool_calls
                })

                if not message.tool_calls:
                    # No tools called, go idle
                    self._update_status(name, "idle")
                    time.sleep(2)  # Wait before next check
                    continue

                # Execute tool calls
                self._update_status(name, "working")
                for tool_call in message.tool_calls:
                    import json as json_mod
                    args = json_mod.loads(tool_call.function.arguments)
                    output = registry.execute(tool_call.function.name, args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": output[:10000],  # Truncate long outputs
                    })

            except Exception as e:
                print(colorize(f"[Team] {name} error: {e}", "bright_red"))
                time.sleep(2)

        # Mark as shutdown
        self._update_status(name, "shutdown")
        print(colorize(f"\n[Team] {name} shutdown", "yellow"))

    def _update_status(self, name: str, status: str):
        """Update teammate status in config."""
        for member in self.config["members"]:
            if member["name"] == name:
                member["status"] = status
                break
        self._save_config()
        if name in self.teammates:
            self.teammates[name].status = status

    def spawn(self, name: str, role: str, prompt: str) -> str:
        """Spawn a new teammate with given name and role."""
        if name in self.teammates:
            return f"Error: Teammate '{name}' already exists"

        # Add to config
        member = {"name": name, "role": role, "status": "working"}
        self.config["members"].append(member)
        self._save_config()

        # Create stop event for graceful shutdown
        stop_event = threading.Event()

        # Start thread
        thread = threading.Thread(
            target=self._teammate_loop,
            args=(name, role, prompt, stop_event),
            daemon=True
        )
        thread.start()

        # Track teammate
        self.teammates[name] = Teammate(
            name=name, role=role, status="working",
            thread=thread, stop_event=stop_event
        )

        return f"Spawned teammate '{name}' (role: {role})"

    def shutdown(self, name: str) -> str:
        """Shutdown a teammate."""
        if name not in self.teammates:
            return f"Error: Teammate '{name}' not found"

        self.teammates[name].stop_event.set()
        self._update_status(name, "shutdown")
        return f"Shutdown teammate '{name}'"

    def list_members(self) -> List[dict]:
        """List all team members."""
        return self.config["members"]

    def get_member_names(self) -> List[str]:
        """Get list of member names."""
        return [m["name"] for m in self.config["members"]]


# ============================================================================
# Tool Definitions
# ============================================================================

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
                "content": {"type": "string", "description": "Message content"}
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
        return bus.send(sender, args["to"], args["content"])
    return handle_send


def create_read_inbox_handler(bus: MessageBus, name: str) -> Callable:
    """Create read_inbox tool handler."""
    def handle_read_inbox(args: dict) -> str:
        return bus.read_inbox(name)
    return handle_read_inbox