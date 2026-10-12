"""Agent Teams - Persistent multi-agent collaboration with message bus."""

import math
import re
from contextlib import contextmanager
import json
import time
import threading
from pathlib import Path
from typing import Dict, List, Callable
from dataclasses import dataclass
from openai import OpenAI

from agent_runtime.config import Settings
from agent_runtime.execution import Task, TaskStopped, current_task, task_scope, completion, tool_results
from agent_runtime.storage import file_lock, atomic_json, SessionStore, repair_history, jsonable

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
        self._contexts = {}
        self._context_lock = threading.Lock()

    @staticmethod
    def validate_name(name: str):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            raise ValueError("Agent name must contain 1-64 letters, digits, underscores or hyphens")

    @contextmanager
    def _inbox_lock(self, name: str):
        self.validate_name(name)
        # File locks also protect separate MessageBus instances/processes.
        with file_lock(self.dir / f"{name}.lock"):
            path = self.dir / f"{name}.jsonl"
            if path.is_symlink():
                raise ValueError("Inbox path may not be a symlink")
            yield path

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
            if msg_type == "message":
                with self._context_lock:
                    self._contexts[to] = current_task.get()
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

    def take_context(self, name):
        with self._context_lock:
            return self._contexts.pop(name, None)

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

    def __init__(self, team_dir: Path, client: OpenAI, model: str, bus: MessageBus, settings=None):
        self.settings = settings or Settings.from_env()
        self.MAX_ITERATIONS = self.settings.max_rounds
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
        self._contexts = {}

    def _load_config(self) -> dict:
        """Load or create team config."""
        if self.config_path.exists():
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        return {"members": []}

    def _save_config(self):
        with self._lock, file_lock(self.config_path.with_suffix(".lock")):
            atomic_json(self.config_path, self.config)

    def _store(self, name):
        self.bus.validate_name(name)
        return SessionStore(self.team_dir / "tasks" / f"{name}.json")

    def _setup_teammate_registry(self, name: str) -> ToolRegistry:
        """Setup registry with teammate tools (bash, read_file, todo)."""
        registry = ToolRegistry()
        registry.register("bash", BASH_TOOL, handle_bash)
        registry.register("read_file", READ_FILE_TOOL, handle_read_file)
        # Each teammate has its own todo manager
        todo_manager = TodoManager()
        registry.register("todo", TODO_TOOL, create_todo_handler(todo_manager))
        registry.todo_manager = todo_manager
        registry.register("send", SEND_TOOL, create_send_handler(self.bus, name))
        registry.register("read_inbox", READ_INBOX_TOOL, create_read_inbox_handler(self.bus, name))
        return registry

    def _teammate_loop(self, name: str, role: str, prompt: str, stop_event: threading.Event):
        """Main loop for a teammate agent."""
        print(colorize(f"\n[Team] {name} ({role}) started", "green"))

        registry = self._setup_teammate_registry(name)
        store = self._store(name)
        snapshot = store.load() if self._contexts[name][1] else None
        messages = repair_history(snapshot["messages"]) if snapshot else []
        if snapshot:
            messages.append({"role": "user", "content": "Resume from the checkpoint. Do not replay completed tools; inspect interrupted actions first."})
        else:
            messages = [{"role": "user", "content": prompt}]
        if not messages or messages[0]["role"] != "system":
            messages.insert(0, {"role": "system", "content": TEAMMATE_SYSTEM})
        todo = registry.todo_manager
        if snapshot:
            todo.update(snapshot.get("todo", []))
        idle = False
        iterations = 0
        task = self._contexts[name][0]
        def save(status):
            store.save(messages, todo.items, status, task.task_id)
        save("running")
        try:
            while not stop_event.is_set():
                inbox = self.bus.read_inbox(name)
                if inbox != "[]":
                    parent = self.bus.take_context(name)
                    messages = repair_history(jsonable(messages))
                    messages.append({"role": "user", "content": f"<inbox>{inbox}</inbox>"})
                    if idle:
                        iterations = 0
                        task = parent.child(stop_event) if parent else Task(self.settings, stop=stop_event)
                        self._update_status(name, "working", task_id=task.task_id, result=None, outcome=None)
                    idle = False
                    save("running")
                if idle:
                    stop_event.wait(0.1)
                    continue
                if iterations >= min(self.MAX_ITERATIONS, self.settings.max_rounds):
                    break
                iterations += 1
                self._update_status(name, "working")
                try:
                    with task_scope(task):
                        task.check()
                        response = completion(self.client, self.model,
                                              messages,
                                              registry, task)
                        message = response.choices[0].message
                        messages.append(jsonable({"role": "assistant", "content": message.content,
                                                  "tool_calls": message.tool_calls}))
                        save("running")
                        if not message.tool_calls:
                            result = message.content or "(no summary)"
                            save("completed")
                            self.bus.send(name, "lead", result, "result", {"task_id": task.task_id})
                            self._update_status(name, "idle", result=result, outcome="completed")
                            idle = True
                            continue
                        def save_result(result):
                            messages.append(result)
                            save("running")
                        tool_results(message, registry, task, save_result)
                        task.check()
                except TaskStopped as exc:
                    self._update_status(name, "shutdown", outcome="stopped", result=str(exc))
                    self.bus.send(name, "lead", f"Task stopped: {exc}", "error", {"task_id": task.task_id})
                    save("stopped")
                    break
                except Exception as exc:
                    # Do not leak API response bodies or request data into logs.
                    error = f"Teammate error ({type(exc).__name__}); resume after resolving the cause."
                    self.bus.send(name, "lead", error, "error", {"task_id": task.task_id})
                    self._update_status(name, "idle", outcome="error", result=error)
                    save("error")
                    idle = True
            if not stop_event.is_set() and not idle and iterations >= min(self.MAX_ITERATIONS, self.settings.max_rounds):
                result = "(teammate reached iteration limit)"
                self.bus.send(name, "lead", result, "error", {"task_id": task.task_id})
                self._update_status(name, "shutdown", outcome="limited", result=result)
                save("limited")
        finally:
            self._update_status(name, "shutdown")
            if stop_event.is_set():
                save("cancelled")
            print(colorize(f"\n[Team] {name} shutdown", "yellow"))

    def _update_status(self, name: str, status: str, **updates):
        with self._lock:
            for member in self.config["members"]:
                if member["name"] == name:
                    member["status"] = status
                    member.update(updates)
                    break
            if name in self.teammates:
                self.teammates[name].status = status
            self._save_config()

    def spawn(self, name: str, role: str, prompt: str, restore=False) -> str:
        self.bus.validate_name(name)
        if name == "lead":
            return "Error: 'lead' is reserved for the main agent"
        with self._lock:
            previous = self.teammates.get(name)
            if previous and previous.thread.is_alive():
                return f"Error: Teammate '{name}' already exists"
            if sum(t.thread.is_alive() for t in self.teammates.values()) >= self.settings.max_teammates:
                return "Error: teammate capacity reached"
            parent = current_task.get()
            stop_event = threading.Event()
            task = parent.child(stop_event) if parent else Task(self.settings, stop=stop_event)
            self._contexts[name] = (task, restore)
            self.config["members"] = [m for m in self.config["members"] if m["name"] != name]
            self.config["members"].append({"name": name, "role": role, "prompt": prompt,
                                            "status": "working", "task_id": task.task_id,
                                            "result": None, "outcome": None})
            self._save_config()
            thread = threading.Thread(target=self._teammate_loop,
                                      args=(name, role, prompt, stop_event), daemon=True)
            self.teammates[name] = Teammate(name, role, "working", thread, stop_event)
            thread.start()
        return json.dumps({"name": name, "role": role, "task_id": task.task_id, "status": "working"})

    def shutdown(self, name: str) -> str:
        with self._lock:
            teammate = self.teammates.get(name)
            if teammate is None:
                return f"Error: Teammate '{name}' not found"
            if not teammate.thread.is_alive():
                return f"Teammate '{name}' is already stopped"
            teammate.stop_event.set()
            self._update_status(name, "stopping", outcome="cancelled")
        teammate.thread.join(timeout=self.settings.api_timeout + 1)
        return f"Shutdown requested for teammate '{name}'"

    def shutdown_all(self):
        with self._lock:
            names = list(self.teammates)
            for teammate in self.teammates.values():
                teammate.stop_event.set()
        for name in names:
            self.shutdown(name)

    def resume(self, name):
        self.bus.validate_name(name)
        with self._lock:
            member = next((dict(m) for m in self.config["members"] if m["name"] == name), None)
        if not member or not self._store(name).load():
            return "Error: no checkpoint for teammate"
        return self.spawn(name, member["role"], member.get("prompt", "Resume saved task"), restore=True)

    def has_pending(self):
        return any(m["status"] in ("working", "stopping") for m in self.list_members())

    def wait(self, name, timeout=30.0):
        self.bus.validate_name(name)
        timeout = float(timeout)
        if timeout < 0 or not math.isfinite(timeout):
            raise ValueError("timeout must be finite and nonnegative")
        timeout = min(timeout, self.settings.team_wait_timeout)
        deadline = time.monotonic() + timeout
        while True:
            task = current_task.get()
            if task:
                task.check()
            member = next((m for m in self.list_members() if m["name"] == name), None)
            if not member:
                return json.dumps({"error": "unknown teammate", "name": name})
            if member["status"] not in ("working", "stopping") or time.monotonic() >= deadline:
                return json.dumps(member)
            time.sleep(min(0.05, max(0, deadline-time.monotonic())))

    def wait_all(self, timeout):
        deadline = time.monotonic() + min(timeout, self.settings.team_wait_timeout)
        for member in self.list_members():
            if member["status"] in ("working", "stopping"):
                self.wait(member["name"], max(0, deadline-time.monotonic()))
        return self.list_members()

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
