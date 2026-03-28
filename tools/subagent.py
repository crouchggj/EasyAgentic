"""Subagent implementation."""

import json
from openai import OpenAI

from .base import ToolRegistry
from .bash import BASH_TOOL, handle_bash
from .read_file import READ_FILE_TOOL, handle_read_file
from .todo import TODO_TOOL, TodoManager, create_todo_handler
from .colors import COLORS, colorize, tool_header, tool_args, tool_output, round_header


SUBAGENT_SYSTEM = """You are a specialized subagent tasked with completing a specific subtask.
You have access to bash commands, file reading, and todo management.
Focus on completing the assigned task efficiently and return a clear summary of your work.
You cannot spawn additional subagents."""


def _subagent_prefix() -> str:
    """Return the green prefix for subagent output."""
    return colorize("│", "green")


class SubAgent:
    """Subagent with limited tools (no task tool to prevent recursion)."""

    MAX_ITERATIONS = 30

    def __init__(self, client: OpenAI, model: str):
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
        """Run subagent loop and return final text result."""
        # Print subagent header
        print(colorize("\n┌─ SubAgent Started ─┐", "green", styles=["bold"]))
        prompt_preview = prompt[:100]
        if len(prompt) > 100:
            prompt_preview += "..."
        print(colorize(f"  Prompt: {prompt_preview}", "dim"))

        messages = [{"role": "system", "content": SUBAGENT_SYSTEM},
                    {"role": "user", "content": prompt}]

        for iteration in range(self.MAX_ITERATIONS):
            print(f"{_subagent_prefix()} {round_header(iteration + 1).strip()}")

            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self.registry.get_definitions(),
                max_tokens=8000,
            )

            message = response.choices[0].message

            # Print thinking preview
            if message.content:
                content_preview = message.content[:150]
                if len(message.content) > 150:
                    content_preview += "..."
                print(f"{_subagent_prefix()} {colorize('[thinking]', 'dim')} {content_preview}")

            messages.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": message.tool_calls
            })

            if not message.tool_calls:
                # Return final text content
                result = message.content or "(no summary)"
                print(colorize("└─ SubAgent Done ─┘", "green", styles=["bold"]))
                result_preview = result[:100]
                if len(result) > 100:
                    result_preview += "..."
                print(colorize(f"  Result: {result_preview}", "dim"))
                return result

            # Execute all tool calls
            for tool_call in message.tool_calls:
                args = json.loads(tool_call.function.arguments)
                tool_name = tool_call.function.name

                # Print tool call with color (indented for subagent)
                print(f"{_subagent_prefix()}")
                print(f"{_subagent_prefix()} {tool_header(tool_name).strip()}")
                for line in tool_args(args).split("\n"):
                    if line:
                        print(f"{_subagent_prefix()}   {line}")

                # Execute and get output
                output = self.registry.execute(tool_name, args)
                output = output[:50000] if len(output) > 50000 else output

                # Print output preview (indented)
                for line in tool_output(output, max_lines=5).split("\n"):
                    if line:
                        print(f"{_subagent_prefix()}   {line}")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": output,
                })

        print(colorize("└─ SubAgent Limit Reached ─┘", "green", styles=["bold"]))
        return "(subagent reached iteration limit)"