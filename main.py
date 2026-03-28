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

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_URL)

# System prompt for the agent
SYSTEM = """You are a helpful AI assistant with access to bash commands and file reading.
You can execute shell commands and read files to help the user with their tasks.
Always be careful when running commands and explain what you're doing."""

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

        print(f'content: {message.content}')
        if not message.tool_calls:
            return message.content
        print(f'tool calls: {message.tool_calls}')
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