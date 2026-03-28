import os
import subprocess
import json
from openai import OpenAI

# Configuration
OPENAI_URL = os.environ.get("OPENAI_URL", "https://api.openai.com/v1")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL = os.environ.get("MODEL", "gpt-4o")

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_URL)

# System prompt for the agent
SYSTEM = """You are a helpful AI assistant with access to bash commands.
You can execute shell commands to help the user with their tasks.
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