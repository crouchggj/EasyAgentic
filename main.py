import os
import json
from openai import OpenAI

from tools import ToolRegistry, TodoManager, setup_registry, SubAgent
from tools.colors import (
    COLORS, TOOL_COLORS, colorize, tool_header, tool_args,
    tool_output, print_tool_call, round_header
)

# Configuration
OPENAI_URL = os.environ.get("OPENAI_URL", "https://api.openai.com/v1")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL = os.environ.get("MODEL", "gpt-4o")
TODO_REMINDER_THRESHOLD = 3

# System prompt
SYSTEM = """You are a helpful AI assistant with access to bash commands, file reading, todo management, and task delegation.
You can execute shell commands, read files, track tasks, and spawn subagents for complex subtasks.
Use the task tool to delegate complex subtasks to specialized subagents.
Always be careful when running commands and explain what you're doing.
Use the todo tool to track progress on multi-step tasks."""


class AgentLoop:
    """Agent loop with decoupled tool execution and subagent support."""

    def __init__(self, client: OpenAI, registry: ToolRegistry, todo_manager: TodoManager, subagent: SubAgent):
        self.client = client
        self.registry = registry
        self.todo_manager = todo_manager
        self.subagent = subagent
        self.todo_skip_count = 0

    def run(self, query: str) -> str:
        """Run the agent loop for a query."""
        self.todo_skip_count = 0
        self.round_num = 0
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": query}
        ]

        while True:
            self.round_num += 1
            print(round_header(self.round_num))

            response = self.client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=self.registry.get_definitions(),
                max_tokens=8000,
            )

            message = response.choices[0].message

            # Print assistant content if present
            if message.content:
                print(f"\n{COLORS['dim']}[thinking]{COLORS['reset']} {message.content[:200]}...")

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
                    print(colorize(f"[REMINDER: {self.todo_skip_count} rounds without todo]", "bright_yellow"))
                    self.todo_skip_count = 0
                    continue

            # Execute all tool calls
            for tool_call in message.tool_calls:
                args = json.loads(tool_call.function.arguments)
                tool_name = tool_call.function.name

                # Print tool call with color
                print(tool_header(tool_name))
                print(tool_args(args))

                # Execute and get output
                output = self.registry.execute(tool_name, args)

                # Print output preview
                print(tool_output(output, max_lines=8))

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": output,
                })


def main():
    """Main entry point."""
    client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_URL)
    todo_manager = TodoManager()
    subagent = SubAgent(client, MODEL)

    # Parent registry includes task tool
    registry = setup_registry(todo_manager, subagent_runner=subagent.run)

    agent = AgentLoop(client, registry, todo_manager, subagent)

    print(colorize("Simple Agent", "bright_cyan") + colorize(" - Type 'quit' to exit", "dim"))
    print(colorize("-" * 40, "dim"))

    while True:
        try:
            query = input(colorize("\nYou: ", "bright_green"))
            if not query:
                continue
            if query.lower() in ["quit", "exit", "q"]:
                print(colorize("Goodbye!", "bright_cyan"))
                break

            result = agent.run(query)
            print(f"\n{COLORS['bright_blue']}{COLORS['bold']}Final Answer:{COLORS['reset']}")
            if result:
                print(result)
        except KeyboardInterrupt:
            print(f"\n{COLORS['bright_cyan']}Goodbye!{COLORS['reset']}")
            break
        except Exception as e:
            print(colorize(f"\nError: {e}", "bright_red"))


if __name__ == "__main__":
    main()