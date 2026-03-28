import os
import json
from pathlib import Path
from openai import OpenAI

from tools import (
    ToolRegistry, TodoManager, setup_registry, SubAgent, SkillLoader,
    MessageBus, TeammateManager
)
from tools.colors import COLORS, colorize, tool_header, tool_args, tool_output, round_header

# Configuration
OPENAI_URL = os.environ.get("OPENAI_URL", "https://api.openai.com/v1")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL = os.environ.get("MODEL", "gpt-4o")
TODO_REMINDER_THRESHOLD = 3
SKILLS_DIR = os.environ.get("SKILLS_DIR", "skills")
TEAM_DIR = Path(os.environ.get("TEAM_DIR", ".team"))


def build_system_prompt(skill_loader: SkillLoader, bus: MessageBus, agent_name: str) -> str:
    """Build system prompt with skills summary and team info (Layer 1)."""
    base = f"""You are a helpful AI assistant named '{agent_name}' with access to bash commands, file reading, todo management, task delegation, skills, and team collaboration.
You can execute shell commands, read files, track tasks, spawn subagents for complex subtasks, load specialized skills, and coordinate with teammates.
Always be careful when running commands and explain what you're doing.
Use the todo tool to track progress on multi-step tasks.
Use the skill tool to load specialized workflows when appropriate.
Use spawn to create new teammates with specific roles.
Use send to communicate with teammates.
Use read_inbox to check for messages from teammates."""

    # Layer 1: Add skills summary to system prompt
    skills_summary = skill_loader.get_skills_summary()
    if skills_summary:
        base += f"\n\n{skills_summary}"

    return base


class AgentLoop:
    """Agent loop with decoupled tool execution and subagent support."""

    def __init__(self, client: OpenAI, registry: ToolRegistry, todo_manager: TodoManager,
                 subagent: SubAgent, skill_loader: SkillLoader,
                 bus: MessageBus, team_manager: TeammateManager, agent_name: str = "lead"):
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
        self.system_prompt = build_system_prompt(skill_loader, bus, agent_name)

    def run(self, query: str) -> str:
        """Run the agent loop for a query."""
        self.todo_skip_count = 0
        self.round_num = 0
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": query}
        ]

        while True:
            self.round_num += 1
            print(round_header(self.round_num))

            # Check inbox before each round
            inbox = self.bus.read_inbox(self.agent_name)
            if inbox != "[]":
                messages.append({
                    "role": "user",
                    "content": f"<inbox>{inbox}</inbox>"
                })

            response = self.client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=self.registry.get_definitions(),
                max_tokens=8000,
            )

            message = response.choices[0].message

            # Print assistant content if present
            if message.content:
                content_preview = message.content[:200]
                if len(message.content) > 200:
                    content_preview += "..."
                print(f"\n{COLORS['dim']}[thinking]{COLORS['reset']} {content_preview}")

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
    skill_loader = SkillLoader(SKILLS_DIR)

    # Setup team
    bus = MessageBus(TEAM_DIR)
    team_manager = TeammateManager(TEAM_DIR, client, MODEL, bus)

    # Print loaded skills
    skills = skill_loader.get_skill_names()
    if skills:
        print(colorize(f"Loaded {len(skills)} skills: ", "cyan") + colorize(", ".join(skills), "dim"))

    # Print team status
    members = team_manager.list_members()
    if members:
        print(colorize(f"Team members: ", "green") + colorize(
            ", ".join(f"{m['name']}({m['role']})" for m in members), "dim"))

    # Parent registry includes all tools
    registry = setup_registry(
        todo_manager,
        subagent_runner=subagent.run,
        skill_loader=skill_loader,
        team_manager=team_manager,
        bus=bus,
        agent_name="lead"
    )

    agent = AgentLoop(client, registry, todo_manager, subagent, skill_loader, bus, team_manager)

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
            print(colorize("\nFinal Answer:", "bright_blue", styles=["bold"]))
            if result:
                print(result)
        except KeyboardInterrupt:
            print(colorize("\nGoodbye!", "bright_cyan"))
            break
        except Exception as e:
            print(colorize(f"\nError: {e}", "bright_red"))


if __name__ == "__main__":
    main()