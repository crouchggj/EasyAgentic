"""Todo tool implementation."""

from typing import Callable

TODO_TOOL = {
    "type": "function",
    "function": {
        "name": "todo",
        "description": "Update and manage todo list for task tracking",
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


class TodoManager:
    """Manage todo items for task tracking."""

    def __init__(self):
        self.items = []

    def update(self, items: list) -> str:
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
        if not self.items:
            return "No tasks"
        lines = ["Current tasks:"]
        for item in self.items:
            status_icon = {"pending": "○", "in_progress": "●", "completed": "✓"}
            icon = status_icon.get(item["status"], "○")
            lines.append(f"  {icon} [{item['id']}] {item['text']} ({item['status']})")
        return "\n".join(lines)


def create_todo_handler(todo_manager: TodoManager) -> Callable[[dict], str]:
    """Create todo handler with todo_manager reference."""
    def handle_todo(args: dict) -> str:
        try:
            return todo_manager.update(args["items"])
        except ValueError as e:
            return f"Error: {str(e)}"
    return handle_todo