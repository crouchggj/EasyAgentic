"""Agent Teams - Persistent multi-agent collaboration with message bus."""

import os
import re
import tempfile
import fcntl
from contextlib import contextmanager
import json
import time
import threading
from pathlib import Path
from typing import Dict, List, Callable
from dataclasses import dataclass
from openai import OpenAI

from .base import ToolRegistry
from .bash import BASH_TOOL, handle_bash
from .read_file import READ_FILE_TOOL, handle_read_file
from .todo import TODO_TOOL, TodoManager, create_todo_handler
from .colors import colorize


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

    @staticmethod
    def validate_name(name: str):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            raise ValueError("Agent name must contain 1-64 letters, digits, underscores or hyphens")

    @contextmanager
    def _inbox_lock(self, name: str):
        self.validate_name(name)
        # File locks also protect separate MessageBus instances/processes.
        with (self.dir / f"{name}.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield self.dir / f"{name}.jsonl"
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

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

        self.validate_name(sender)
        with self._inbox_lock(to) as inbox_path:
            with inbox_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        return f"Message sent to {to}"

    def read_inbox(self, name: str) -> str:
        """Read and drain inbox for a teammate."""
        with self._inbox_lock(name) as inbox_path:
            if not inbox_path.exists():
                return "[]"
            lines = inbox_path.read_text(encoding="utf-8").splitlines()
            msgs = [json.loads(line) for line in lines if line.strip()]
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

        self._lock = threading.RLock()

        # Setup config
        self.config_path = team_dir / "config.json"
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config = self._load_config()
        # Persisted members do not imply live threads after restart.
        for member in self.config["members"]:
            member["status"] = "shutdown"
        self._save_config()

        # Track teammates
        self.teammates: Dict[str, Teammate] = {}

    def _load_config(self) -> dict:
        """Load or create team config."""
        if self.config_path.exists():
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        return {"members": []}

    def _save_config(self):
        """Serialize writes and atomically replace the configuration."""
        with self._lock:
            fd, path = tempfile.mkstemp(dir=self.team_dir, prefix="config-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(self.config, stream, indent=2, ensure_ascii=False)
                os.replace(path, self.config_path)
            finally:
                if os.path.exists(path):
                    os.unlink(path)

    def _setup_teammate_registry(self, name: str) -> ToolRegistry:
        """Setup registry with teammate tools (bash, read_file, todo)."""
        registry = ToolRegistry()
        registry.register("bash", BASH_TOOL, handle_bash)
        registry.register("read_file", READ_FILE_TOOL, handle_read_file)
        # Each teammate has its own todo manager
        todo_manager = TodoManager()
        registry.register("todo", TODO_TOOL, create_todo_handler(todo_manager))
        registry.register("send", SEND_TOOL, create_send_handler(self.bus, name))
        registry.register("read_inbox", READ_INBOX_TOOL, create_read_inbox_handler(self.bus, name))
        return registry

    def _teammate_loop(self, name: str, role: str, prompt: str, stop_event: threading.Event):
        """Main loop for a teammate agent."""
        print(colorize(f"\n[Team] {name} ({role}) started", "green"))

        registry = self._setup_teammate_registry(name)
        messages = [{"role": "user", "content": prompt}]
        idle = False
        iterations = 0
        try:
            while not stop_event.is_set() and iterations < self.MAX_ITERATIONS:
                inbox = self.bus.read_inbox(name)
                if inbox != "[]":
                    messages.append({"role": "user", "content": f"<inbox>{inbox}</inbox>"})
                    if idle:
                        iterations = 0
                    idle = False
                if idle:
                    stop_event.wait(0.1)
                    continue
                iterations += 1
                self._update_status(name, "working")
                try:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=[{"role": "system", "content": TEAMMATE_SYSTEM}] + messages,
                        tools=registry.get_definitions(), max_tokens=4000,
                    )
                    message = response.choices[0].message
                    messages.append({"role": "assistant", "content": message.content,
                                     "tool_calls": message.tool_calls})
                    if not message.tool_calls:
                        self.bus.send(name, "lead", message.content or "(no summary)", "result")
                        idle = True
                        self._update_status(name, "idle")
                        continue
                    for tool_call in message.tool_calls:
                        output = registry.execute_json(tool_call.function.name, tool_call.function.arguments)
                        messages.append({"role": "tool", "tool_call_id": tool_call.id,
                                         "content": output[:10000]})
                except Exception as exc:
                    self.bus.send(name, "lead", f"Teammate error: {exc}", "error")
                    # Wait for fresh instructions rather than retrying a broken history.
                    messages = []
                    idle = True
                    self._update_status(name, "idle")
            if not stop_event.is_set():
                self.bus.send(name, "lead", "(teammate reached iteration limit)", "error")
        finally:
            self._update_status(name, "shutdown")
            print(colorize(f"\n[Team] {name} shutdown", "yellow"))

    def _update_status(self, name: str, status: str):
        with self._lock:
            for member in self.config["members"]:
                if member["name"] == name:
                    member["status"] = status
                    break
            if name in self.teammates:
                self.teammates[name].status = status
            self._save_config()

    def spawn(self, name: str, role: str, prompt: str) -> str:
        self.bus.validate_name(name)
        if name == "lead":
            return "Error: 'lead' is reserved for the main agent"
        with self._lock:
            previous = self.teammates.get(name)
            if previous and previous.thread.is_alive():
                return f"Error: Teammate '{name}' already exists"
            self.config["members"] = [m for m in self.config["members"] if m["name"] != name]
            self.config["members"].append({"name": name, "role": role, "status": "working"})
            self._save_config()
            stop_event = threading.Event()
            thread = threading.Thread(target=self._teammate_loop,
                                      args=(name, role, prompt, stop_event), daemon=True)
            self.teammates[name] = Teammate(name, role, "working", thread, stop_event)
            thread.start()
        return f"Spawned teammate '{name}' (role: {role})"

    def shutdown(self, name: str) -> str:
        with self._lock:
            teammate = self.teammates.get(name)
            if teammate is None:
                return f"Error: Teammate '{name}' not found"
            teammate.stop_event.set()
        teammate.thread.join(timeout=2)
        return f"Shutdown requested for teammate '{name}'"

    def shutdown_all(self):
        with self._lock:
            names = list(self.teammates)
            for teammate in self.teammates.values():
                teammate.stop_event.set()
        for name in names:
            self.shutdown(name)

    def list_members(self) -> List[dict]:
        with self._lock:
            return [dict(member) for member in self.config["members"]]

    def get_member_names(self) -> List[str]:
        return [m["name"] for m in self.list_members()]


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
