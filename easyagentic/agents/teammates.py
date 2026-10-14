"""Background teammate lifecycle, checkpoints and result publication."""
import math
import json
import time
import threading
from pathlib import Path
from typing import Dict, List
from dataclasses import dataclass
from openai import OpenAI

from agent_runtime.journal import TaskJournal
from agent_runtime.config import Settings
from agent_runtime.execution import Task, TaskStopped, current_task, task_scope, completion, tool_results
from agent_runtime.storage import file_lock, atomic_json, SessionStore, repair_history, jsonable
from agent_runtime.messages import MessageBus
from tools.base import ToolRegistry
from tools.registry import setup_registry
from tools.todo import TodoManager
from tools.colors import colorize


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
        todo_manager = TodoManager()
        registry = setup_registry(todo_manager, bus=self.bus, agent_name=name)
        registry.todo_manager = todo_manager
        return registry

    def _run_teammate_thread(self, name, role, prompt, stop_event):
        try:
            self._teammate_loop(name, role, prompt, stop_event)
        except Exception as exc:
            task = self._contexts[name][0]
            error = f"Teammate infrastructure failed ({type(exc).__name__}); inspect checkpoint and delivery state before resuming."
            self._update_status(name, "shutdown", outcome="error", result=error)
            TaskJournal(self.settings.state_path).record(task, "error", actor=name)
            try:
                self.bus.send(name, "lead", error, "error", {"task_id": task.task_id}, message_id="failure:" + task.task_id)
            except Exception:
                # The retained member outcome still exposes failures while the queue is unavailable.
                pass

    def _teammate_loop(self, name: str, role: str, prompt: str, stop_event: threading.Event):
        """Main loop for a teammate agent."""
        print(colorize(f"\n[Team] {name} ({role}) started", "green"))

        registry = self._setup_teammate_registry(name)
        store = self._store(name)
        prior = store.load()
        snapshot = prior if self._contexts[name][1] else None
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
        seen = set(prior.get("received_ids", [])) if prior else set()
        registry.seen_messages = seen
        idle = False
        iterations = 0
        task = self._contexts[name][0]
        outbox = list(snapshot.get("outbox", [])) if snapshot else []
        saved_status = None
        def save(status, received=None):
            nonlocal saved_status
            store.save(messages, todo.items, status, task.task_id,
                       registry.received_ids() if received is None else received, outbox=outbox)
            TaskJournal(self.settings.state_path).record(task, status, actor=name)
            saved_status = status
        if not (snapshot and snapshot["status"] == "completed"):
            save("running")
        if snapshot and snapshot["status"] == "completed":
            result = next((m.get("content") for m in reversed(messages) if m["role"] == "assistant" and not m.get("tool_calls")), "(no summary)")
            if not outbox:
                outbox = [{"id": "result:" + snapshot["task_id"], "content": result, "task_id": snapshot["task_id"]}]
            save("completed")
            try:
                self._publish_outbox(name, outbox)
                self._update_status(name, "shutdown", outcome="completed", result=result)
            except Exception:
                self._update_status(name, "shutdown", outcome="delivery_pending", result=result)
                TaskJournal(self.settings.state_path).record(task, "delivery_pending", actor=name)
            return
        try:
            while not stop_event.is_set():
                def accept_inbox(inbox, received):
                    nonlocal messages, task, idle, iterations, outbox
                    previous = (messages, task, idle, iterations, outbox)
                    messages = repair_history(jsonable(messages)) + [{"role": "user", "content": f"<inbox>{inbox}</inbox>"}]
                    if idle:
                        parent = self.bus.take_context(name)
                        task = parent.child(stop_event) if parent else Task(self.settings, stop=stop_event)
                        iterations = 0
                        outbox = []
                    idle = False
                    try:
                        save("running", received)
                        self._contexts[name] = (task, False)
                        self._update_status(name, "working", task_id=task.task_id, result=None, outcome=None, pending_reviews=[])
                    except BaseException:
                        messages, task, idle, iterations, outbox = previous
                        raise
                self.bus.receive(name, seen, accept_inbox)
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
                            outbox = [{"id": "result:" + task.task_id, "content": result, "task_id": task.task_id}]
                            save("completed")
                            idle = True
                            try:
                                self._publish_outbox(name, outbox)
                            except Exception:
                                self._update_status(name, "shutdown", outcome="delivery_pending", result=result)
                                TaskJournal(self.settings.state_path).record(task, "delivery_pending", actor=name)
                                break
                            self._update_status(name, "idle", result=result, outcome="completed")
                            continue
                        def save_result(result):
                            messages.append(result)
                            save("running")
                        tool_results(message, registry, task, save_result)
                        if task.pending_reviews:
                            self._update_status(name, "awaiting_review", outcome="awaiting_review", result=", ".join(task.pending_reviews), pending_reviews=list(task.pending_reviews))
                            save("awaiting_review")
                            idle = True
                            continue
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
            if stop_event.is_set() and saved_status not in ("completed", "awaiting_review"):
                save("cancelled")
            print(colorize(f"\n[Team] {name} shutdown", "yellow"))

    def _publish_outbox(self, name, outbox):
        for entry in outbox:
            self.bus.send(name, "lead", entry["content"], "result", {"task_id": entry["task_id"]}, message_id=entry["id"])

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
            thread = threading.Thread(target=self._run_teammate_thread,
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

    def notify_review(self, request):
        for member in self.list_members():
            if member["status"] == "awaiting_review" and (member.get("task_id") == request.get("task_id") or request["id"] in member.get("pending_reviews", [])):
                self.bus.send("lead", member["name"], f"Operator {request['status']} review {request['id']}. Resubmit only the exact approved operation; never retry a rejected operation.")

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
