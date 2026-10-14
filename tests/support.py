"""Explicit operator decisions for shell integration tests, never model auto-approval."""
from contextlib import contextmanager
import threading
import time
from types import SimpleNamespace

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


@contextmanager
def cancel_when_ready(task, marker, timeout=5):
    """Cancel only after the real command signals readiness in its workspace."""
    finished = threading.Event()
    state = SimpleNamespace(ready=threading.Event(), cancelled_at=None)
    def cancel():
        deadline = time.monotonic() + timeout
        while not finished.is_set() and time.monotonic() < deadline:
            if marker.exists():
                state.cancelled_at = time.monotonic()
                state.ready.set()
                task.stop.set()
                return
            finished.wait(0.01)
        if not finished.is_set():
            # Clean up failed starts too; callers must assert the readiness signal.
            task.stop.set()
    worker = threading.Thread(target=cancel, daemon=True)
    worker.start()
    try:
        yield state
    finally:
        finished.set()
        worker.join(timeout=2)
