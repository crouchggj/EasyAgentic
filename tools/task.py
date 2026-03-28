"""Task tool for spawning subagents."""

import json

TASK_TOOL = {
    "type": "function",
    "function": {
        "name": "task",
        "description": "Spawn a subagent to handle a complex subtask. The subagent has access to bash, read_file, and todo tools but cannot spawn further subagents.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The task description and instructions for the subagent"
                }
            },
            "required": ["prompt"]
        }
    }
}


def create_task_handler(subagent_runner) -> callable:
    """Create task handler with subagent runner reference."""
    def handle_task(args: dict) -> str:
        prompt = args["prompt"]
        return subagent_runner(prompt)
    return handle_task