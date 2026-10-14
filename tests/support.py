"""Explicit operator decisions for shell integration tests, never model auto-approval."""
from agent_runtime.config import Settings
from agent_runtime.execution import current_task
from agent_runtime.review import ReviewRequired, review_queue
from tools.bash import handle_bash


def reviewed_shell(args):
    try:
        return handle_bash(args)
    except ReviewRequired as request:
        task = current_task.get()
        settings = task.settings if task else Settings.from_env()
        queue = review_queue(settings)
        queue.preview(request.request_id)
        queue.decide(request.request_id, True)
        return handle_bash(args)
