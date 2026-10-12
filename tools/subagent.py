"""Subagent implementation."""

from openai import OpenAI
from agent_runtime.config import Settings
from agent_runtime.execution import Task, TaskStopped, current_task, task_scope, completion, tool_results
from agent_runtime.storage import jsonable

from .base import ToolRegistry
from .bash import BASH_TOOL, handle_bash
from .read_file import READ_FILE_TOOL, handle_read_file
from .todo import TODO_TOOL, TodoManager, create_todo_handler


SUBAGENT_SYSTEM = """You are a specialized subagent tasked with completing a specific subtask.
You have access to bash commands, file reading, and todo management.
Focus on completing the assigned task efficiently and return a clear summary of your work.
You cannot spawn additional subagents."""


class SubAgent:
    """Subagent with limited tools (no task tool to prevent recursion)."""

    MAX_ITERATIONS = 30

    def __init__(self, client: OpenAI, model: str, settings=None):
        self.settings = settings or Settings.from_env()
        self.MAX_ITERATIONS = self.settings.subagent_rounds
        self.client = client
        self.model = model
        self.todo_manager = TodoManager()
        self.registry = self._setup_registry()

    def _setup_registry(self) -> ToolRegistry:
        """Setup registry with child tools (excluding task)."""
        registry = ToolRegistry()
        registry.register("bash", BASH_TOOL, handle_bash)
        registry.register("read_file", READ_FILE_TOOL, handle_read_file)
        registry.register("todo", TODO_TOOL, create_todo_handler(self.todo_manager))
        return registry

    def run(self, prompt: str) -> str:
        """Children share their parent's budget and deadline."""
        parent = current_task.get()
        task = parent.child() if parent else Task(self.settings)
        self.todo_manager.items = []
        messages = [{"role": "system", "content": SUBAGENT_SYSTEM},
                    {"role": "user", "content": prompt}]
        with task_scope(task):
            try:
                for _ in range(min(self.MAX_ITERATIONS, self.settings.subagent_rounds)):
                    response = completion(self.client, self.model, messages, self.registry, task)
                    message = response.choices[0].message
                    messages.append(jsonable({"role": "assistant", "content": message.content,
                                              "tool_calls": message.tool_calls}))
                    if not message.tool_calls:
                        return message.content or "(no summary)"
                    messages.extend(tool_results(message, self.registry, task))
                    task.check()
                return "(subagent reached iteration limit)"
            except TaskStopped as exc:
                return f"Task stopped: {exc}"
