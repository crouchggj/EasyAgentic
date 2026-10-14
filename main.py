import os
from pathlib import Path
from openai import OpenAI
from agent_runtime.journal import TaskJournal
from agent_runtime.review import review_queue
from agent_runtime.client import CancellableClient
from agent_runtime.config import Settings
from agent_runtime.execution import Task, TaskStopped, current_task, task_scope, completion, tool_results
from agent_runtime.storage import SessionStore, repair_history, jsonable

from tools import (
    ToolRegistry, TodoManager, setup_registry, SubAgent, SkillLoader,
    MessageBus, TeammateManager
)
from tools.colors import round_header

# Configuration
OPENAI_URL = os.environ.get("OPENAI_URL", "https://api.openai.com/v1")
TODO_REMINDER_THRESHOLD = 3
SKILLS_DIR = os.environ.get("SKILLS_DIR", "skills")


def build_system_prompt(skill_loader: SkillLoader, bus: MessageBus, agent_name: str) -> str:
    """Build system prompt with skills summary and team info (Layer 1)."""
    base = f"""You are a helpful AI assistant named '{agent_name}' with access to bash commands, file reading, todo management, task delegation, skills, and team collaboration.
You can execute shell commands, read files, track tasks, spawn subagents for complex subtasks, load specialized skills, and coordinate with teammates.
Always be careful when running commands and explain what you're doing.
Use the todo tool to track progress on multi-step tasks.
Use the skill tool to load specialized workflows when appropriate.
Use spawn to create new teammates with specific roles.
Use send to communicate with teammates.
Use read_inbox to check for messages from teammates.
Use team_status and team_wait to check progress and collect results before finalizing.
Use team_cancel to stop work and team_resume to explicitly restore a saved teammate.
Use write_file for file edits; execution requires operator review of the exact diff.
Shell execution follows operator policy and operator review; do not bypass either.
If a review is pending, stop work and wait for the operator; never approve your own actions."""

    # Layer 1: Add skills summary to system prompt
    skills_summary = skill_loader.get_skills_summary()
    if skills_summary:
        base += f"\n\n{skills_summary}"

    return base


class AgentLoop:
    """Agent loop with decoupled tool execution and subagent support."""

    MAX_ITERATIONS = 50

    def __init__(self, client: OpenAI, registry: ToolRegistry, todo_manager: TodoManager,
                 subagent: SubAgent, skill_loader: SkillLoader,
                 bus: MessageBus, team_manager: TeammateManager, agent_name: str = "lead", settings=None, store=None):
        self.settings = settings or Settings.from_env()
        self.MAX_ITERATIONS = self.settings.max_rounds
        self.store = store
        self.client = client
        self.registry = registry
        self.todo_manager = todo_manager
        self.subagent = subagent
        self.skill_loader = skill_loader
        self.bus = bus
        self.team_manager = team_manager
        self.agent_name = agent_name
        self.todo_skip_count = 0
        self.round_num = 0
        self.messages = []
        saved = self.store.load() if self.store else None
        self.received_ids = set(saved.get("received_ids", [])) if saved else set()
        self.registry.seen_messages = self.received_ids
        self.system_prompt = build_system_prompt(skill_loader, bus, agent_name)

    def checkpoint(self, status, task=None, received=None):
        if self.store:
            self.store.save(self.messages, self.todo_manager.items, status,
                            task.task_id if task else None,
                            self.registry.received_ids() if received is None else received)
        if task:
            TaskJournal(self.settings.state_path).record(task, status)

    def restore(self):
        if not self.store:
            return "No session store configured"
        data = self.store.load()
        if not data:
            return "No saved session"
        self.messages = repair_history(data["messages"])
        self.received_ids = set(data.get("received_ids", []))
        self.registry.seen_messages = self.received_ids
        self.todo_manager.update(data.get("todo", []))
        self.checkpoint("restored")
        return "Session restored. Use /continue to resume without replaying completed tools."

    def reset(self):
        self.messages = []
        self.received_ids = set()
        self.registry.seen_messages = self.received_ids
        self.todo_manager.items = []
        self.checkpoint("reset")

    def receive_inbox(self, task):
        def accept(inbox, received):
            self.messages.append({"role": "user", "content": f"<inbox>{inbox}</inbox>"})
            try:
                self.checkpoint("running", task, received)
            except BaseException:
                self.messages.pop()
                raise
        return self.bus.receive(self.agent_name, self.received_ids, accept)

    def run(self, query: str) -> str:
        task = current_task.get() or Task(self.settings)
        with task_scope(task):
            try:
                return self._run(query, task)
            except TaskStopped as exc:
                task.stop.set()
                self.checkpoint("stopped", task)
                return f"Task stopped: {exc}"
            except BaseException:
                task.stop.set()
                self.checkpoint("interrupted", task)
                raise

    def _run(self, query, task):
        self.todo_skip_count = 0
        self.round_num = 0
        self.registry.seen_messages = self.received_ids
        if not self.messages or self.messages[0]["role"] != "system":
            self.messages.insert(0, {"role": "system", "content": self.system_prompt})
        messages = self.messages
        messages.append({"role": "user", "content": query})
        self.checkpoint("running", task)
        for _ in range(min(self.MAX_ITERATIONS, self.settings.max_rounds)):
            self.round_num += 1
            print(round_header(self.round_num))
            self.receive_inbox(task)
            response = completion(self.client, self.settings.model, messages, self.registry, task)
            message = response.choices[0].message
            messages.append(jsonable({"role": "assistant", "content": message.content,
                                      "tool_calls": message.tool_calls}))
            self.checkpoint("running", task)
            if task.pending_reviews and not message.tool_calls:
                self.checkpoint("awaiting_review", task)
                return "Awaiting operator review: " + ", ".join(task.pending_reviews)
            if not message.tool_calls:
                if self.team_manager:
                    if self.team_manager.has_pending():
                        self.team_manager.wait_all(self.settings.team_wait_timeout)
                    if task.pending_reviews:
                        self.checkpoint("awaiting_review", task)
                        return "Awaiting operator review: " + ", ".join(task.pending_reviews)
                    if self.receive_inbox(task):
                        continue
                    if self.team_manager.has_pending():
                        self.checkpoint("waiting", task)
                        return (message.content or "") + "\n[Teammates still pending; use team_status/team_wait or /continue.]"
                self.checkpoint("completed", task)
                return message.content or ""
            todo_called = any(tc.function.name == "todo" for tc in message.tool_calls)
            self.todo_skip_count = 0 if todo_called else self.todo_skip_count + 1
            def save_result(result):
                messages.append(result)
                self.checkpoint("running", task)
            tool_results(message, self.registry, task, save_result)
            if task.pending_reviews:
                self.checkpoint("awaiting_review", task)
                return "Awaiting operator review: " + ", ".join(task.pending_reviews)
            task.check()
            if self.todo_skip_count >= TODO_REMINDER_THRESHOLD and self.todo_manager.items:
                messages.append({"role": "user", "content": "[REMINDER] Update active todo tasks.\n" + self.todo_manager.render()})
                self.todo_skip_count = 0
        task.stop.set()
        self.checkpoint("limited", task)
        return "(agent reached iteration limit)"


def main():
    """Interactive entry point; credentials remain in the process environment."""
    settings = Settings.from_env()
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("EASYAGENTIC_API_KEY")
    if not key:
        raise SystemExit("Set OPENAI_API_KEY (or EASYAGENTIC_API_KEY) securely before starting.")
    client = CancellableClient(api_key=key, base_url=OPENAI_URL, timeout=settings.api_timeout, max_retries=0)
    todo_manager = TodoManager()
    subagent = SubAgent(client, settings.model, settings=settings)
    skill_loader = SkillLoader(SKILLS_DIR)
    team_dir = Path(os.environ.get("TEAM_DIR", str(settings.state_path / "team")))
    bus = MessageBus(team_dir, settings.message_lease, settings.message_attempts)
    team_manager = TeammateManager(team_dir, client, settings.model, bus, settings=settings)
    registry = setup_registry(todo_manager, subagent_runner=subagent.run, skill_loader=skill_loader,
                              team_manager=team_manager, bus=bus, agent_name="lead")
    agent = AgentLoop(client, registry, todo_manager, subagent, skill_loader, bus, team_manager,
                      settings=settings, store=SessionStore(settings.state_path / "session.json"))
    print(f"Loaded {len(skill_loader.get_skill_names())} skills: " + ", ".join(skill_loader.get_skill_names()))
    journal = TaskJournal(settings.state_path)
    journal.recover()
    reviews = review_queue(settings)
    print("Simple Agent - /reviews, /review ID, /approve ID, /reject ID; /resume, /continue, /reset, quit")
    try:
        while True:
            try:
                query = input("\nYou: ").strip()
                if not query:
                    continue
                if query.lower() in ("quit", "exit", "q"):
                    print("Goodbye!")
                    break
                if query == "/tasks":
                    import json
                    print(json.dumps(journal.list(), indent=2))
                    continue
                if query == "/reviews":
                    print("\n".join(f"{r['id']} {r['status']} {r['operation']['tool']}" for r in reviews.requests()) or "No review requests")
                    continue
                if query.startswith("/review "):
                    print(reviews.preview(query.split(maxsplit=1)[1]))
                    continue
                if query.startswith(("/approve ", "/reject ")):
                    request = reviews.decide(query.split(maxsplit=1)[1], query.startswith("/approve "))
                    agent.messages.append({"role": "user", "content": f"Operator {request['status']} review {request['id']}. Resubmit only the exact approved operation."})
                    agent.checkpoint("review_decided")
                    team_manager.notify_review(request)
                    print(request['status'] + "; use /continue for lead work")
                    continue
                if query == "/messages":
                    import json
                    print(json.dumps(bus.status(), indent=2))
                    continue
                if query.startswith("/message-retry "):
                    print(bus.retry(query.split(maxsplit=1)[1]))
                    continue
                if query == "/reset":
                    team_manager.shutdown_all()
                    agent.bus.discard("lead")
                    agent.reset()
                    print("Session reset")
                    continue
                if query == "/resume":
                    print(agent.restore())
                    continue
                if query.startswith("/team-resume "):
                    print(team_manager.resume(query.split(maxsplit=1)[1]))
                    continue
                if query == "/continue":
                    query = "Continue the saved task; inspect existing results and do not repeat completed actions."
                print("\nFinal Answer:\n" + agent.run(query))
            except EOFError:
                print("Goodbye!")
                break
            except KeyboardInterrupt:
                print("Task interrupted; use /continue or /reset, or quit.")
            except (ValueError, FileNotFoundError) as exc:
                if query.startswith(("/review", "/approve", "/reject", "/message-retry", "/team-resume")):
                    print(f"Command refused: {exc}")
                else:
                    print(f"Request failed ({type(exc).__name__}); use /continue after resolving the cause.")
            except Exception as exc:
                # Error bodies may contain request data; log only the class.
                print(f"Request failed ({type(exc).__name__}); use /continue after resolving the cause.")
    finally:
        team_manager.shutdown_all()
        client.close()


if __name__ == "__main__":
    main()
