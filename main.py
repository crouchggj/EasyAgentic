import os
import subprocess
import json
from pathlib import Path
from openai import OpenAI

# Configuration
OPENAI_URL = os.environ.get("OPENAI_URL", "https://api.openai.com/v1")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL = os.environ.get("MODEL", "gpt-4o")
WORKDIR = Path(os.environ.get("WORKDIR", ".")).resolve()

# Todo reminder threshold
TODO_REMINDER_THRESHOLD = 3


class TodoManager:
    """Manage todo items for task tracking."""

    def __init__(self):
        self.items = []

    def update(self, items: list) -> str:
        """Update todo items with validation."""
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
        """Render todo items as formatted string."""
        if not self.items:
            return "No tasks"
        lines = ["Current tasks:"]
        for item in self.items:
            status_icon = {"pending": "○", "in_progress": "●", "completed": "✓"}
            icon = status_icon.get(item["status"], "○")
            lines.append(f"  {icon} [{item['id']}] {item['text']} ({item['status']})")
        return "\n".join(lines)

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_URL)

# Global state
todo_manager = TodoManager()
todo_skip_count = 0  # Count rounds without todo call

# System prompt for the agent
SYSTEM = """You are a helpful AI assistant with access to bash commands, file reading, and todo management.
You can execute shell commands, read files, and track tasks with the todo tool.
Always be careful when running commands and explain what you're doing.
Use the todo tool to track progress on multi-step tasks."""

# Tool definitions
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a bash command and return the output",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the workspace and return its contents",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The relative path to the file to read"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Optional limit on number of lines to read"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "todo",
            "description": "Update and manage todo list for task tracking. Call this when working on multi-step tasks to track progress.",
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
]


def run_bash(command: str) -> str:
    """Execute a bash command and return the output."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=120
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


def safe_path(p: str) -> Path:
    """Validate and resolve a path within the workspace."""
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_read(path: str, limit: int = None) -> str:
    """Read a file from the workspace."""
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


def agent_loop(query):
    """Main agent loop that processes queries and uses tools."""
    global todo_skip_count
    todo_skip_count = 0  # Reset at start of new query

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": query}
    ]

    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            max_tokens=8000,
        )

        message = response.choices[0].message
        messages.append({"role": "assistant", "content": message.content, "tool_calls": message.tool_calls})

        if not message.tool_calls:
            return message.content
        print(f'tool calls: {message.tool_calls}')

        # Check if todo was called in this round
        todo_called = any(tc.function.name == "todo" for tc in message.tool_calls)
        if todo_called:
            todo_skip_count = 0
        else:
            todo_skip_count += 1
            # Inject reminder after 3+ rounds without todo
            if todo_skip_count >= TODO_REMINDER_THRESHOLD and todo_manager.items:
                reminder = f"\n[REMINDER] You have active tasks but haven't updated todo for {todo_skip_count} rounds. Current tasks:\n{todo_manager.render()}\nConsider calling todo to track progress."
                messages.append({"role": "user", "content": reminder})
                print(f'[REMINDER injected: {todo_skip_count} rounds without todo]')
                todo_skip_count = 0  # Reset after reminder
                continue  # Skip tool processing, get next response

        # Process tool calls
        for tool_call in message.tool_calls:
            if tool_call.function.name == "bash":
                args = json.loads(tool_call.function.arguments)
                output = run_bash(args["command"])

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": output,
                })
            elif tool_call.function.name == "read_file":
                args = json.loads(tool_call.function.arguments)
                output = run_read(args["path"], args.get("limit"))

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": output,
                })
            elif tool_call.function.name == "todo":
                args = json.loads(tool_call.function.arguments)
                try:
                    output = todo_manager.update(args["items"])
                except ValueError as e:
                    output = f"Error: {str(e)}"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": output,
                })


def main():
    """Main entry point for the agent."""
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
            result = agent_loop(query)
            if result:
                print(result)
        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")


if __name__ == "__main__":
    main()