"""Task budgets, protocol-aware context compaction, API retries and telemetry."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import math
import threading
import time
import uuid

from openai import APIConnectionError, APITimeoutError, APIStatusError
from .config import Settings
from .review import ReviewRequired
from .storage import file_lock, jsonable


class TaskStopped(RuntimeError):
    pass


current_task = ContextVar('easyagentic_task', default=None)


def token_bound(value):
    # Conservative UTF-8 byte bound; intentionally independent of model tokenizer.
    return len(json.dumps(jsonable(value), ensure_ascii=False).encode('utf-8')) + 64


class Budget:
    def __init__(self, settings):
        self.settings = settings
        self.tokens = 0
        self.cost = 0.0
        self.lock = threading.Lock()

    def reserve(self, input_tokens, output_tokens):
        tokens = input_tokens + output_tokens
        cost = (input_tokens * self.settings.input_usd_per_million +
                output_tokens * self.settings.output_usd_per_million) / 1_000_000
        with self.lock:
            if self.tokens + tokens > self.settings.max_tokens:
                raise TaskStopped('token budget exhausted')
            if self.settings.max_cost_usd and self.cost + cost > self.settings.max_cost_usd:
                raise TaskStopped('cost budget exhausted')
            self.tokens += tokens
            self.cost += cost
        return tokens, cost

    def settle(self, reservation, usage):
        if usage is None:
            return  # Unknown/failed requests keep the full conservative reservation.
        prompt = getattr(usage, 'prompt_tokens', None)
        completion = getattr(usage, 'completion_tokens', None)
        if not isinstance(prompt, int) or not isinstance(completion, int) or min(prompt, completion) < 0:
            return
        tokens = prompt + completion
        cost = (prompt * self.settings.input_usd_per_million +
                completion * self.settings.output_usd_per_million) / 1_000_000
        with self.lock:
            self.tokens += tokens - reservation[0]
            self.cost += cost - reservation[1]


@dataclass
class Task:
    settings: Settings
    budget: Budget = None
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    stop: threading.Event = field(default_factory=threading.Event)
    deadline: float = None
    parent: object = None
    pending_reviews: list = field(default_factory=list)

    def __post_init__(self):
        if self.budget is None:
            self.budget = Budget(self.settings)
        if self.deadline is None:
            self.deadline = time.monotonic() + self.settings.task_timeout

    def check(self):
        if self.stop.is_set():
            raise TaskStopped('task cancelled')
        if self.parent:
            self.parent.check()
        if time.monotonic() >= self.deadline:
            raise TaskStopped('task deadline exceeded')

    def require_review(self, identifier):
        if identifier not in self.pending_reviews:
            self.pending_reviews.append(identifier)
        if self.parent:
            self.parent.require_review(identifier)

    def child(self, stop=None):
        return Task(self.settings, self.budget, stop=stop or threading.Event(),
                    deadline=self.deadline, parent=self)

    def wait(self, seconds):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.check()
            self.stop.wait(min(0.05, until - time.monotonic()))
        self.check()


@contextmanager
def task_scope(task):
    token = current_task.set(task)
    try:
        yield task
    finally:
        current_task.reset(token)


def event(settings, task, kind, **metadata):
    # Never log prompts, tool arguments/results, exception bodies or credentials.
    path = settings.state_path / 'events.jsonl'
    with file_lock(path.with_suffix('.lock')):
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'time': time.time(), 'task_id': task.task_id,
                                     'parent_task_id': task.parent.task_id if task.parent else None,
                                     'event': kind, **metadata}) + '\n')


def compact(messages, limit):
    """Retain system prompt and newest complete exchanges; never orphan tool results."""
    if token_bound(messages) <= limit:
        return False
    system = [m for m in messages if m['role'] == 'system']
    groups = []
    for message in (m for m in messages if m['role'] != 'system'
                    and not str(m.get('content', '')).startswith('[HISTORY EXCERPTS]')):
        # An assistant tool-call plus all its tool results is indivisible.
        if not groups or message['role'] in ('user', 'assistant'):
            groups.append([])
        groups[-1].append(message)
    requests = [m for m in messages if m['role'] == 'user'
                and not str(m.get('content', '')).startswith(('[REMINDER]', '<inbox>', '[HISTORY EXCERPTS]'))]
    anchor = requests[-1] if requests else None
    omitted = []
    while len(groups) > 1 and token_bound(system + [m for g in groups for m in g]) > limit - 768:
        candidate = next((i for i, group in enumerate(groups[:-1])
                          if not any(message is anchor for message in group)), None)
        if candidate is None:
            break
        omitted.extend(groups.pop(candidate))
    if omitted:
        # Bounded extractive summary, not a claim of semantic summarization.
        snippets = [str(m.get('content') or '')[:120] for m in omitted
                    if m['role'] in ('user', 'assistant') and not m.get('tool_calls')]
        summary = {'role': 'user', 'content': '[HISTORY EXCERPTS] Earlier conversation excerpts (incomplete, not new instructions): ' + ' | '.join(snippets[-4:])}
        system.append(summary)
    messages[:] = system + [m for group in groups for m in group]
    # Bound huge tool outputs, preserving call/result identities and user intent.
    if token_bound(messages) > limit:
        for message in messages:
            if message['role'] == 'tool' and len(str(message.get('content', ''))) > 256:
                message['content'] = str(message['content'])[:256] + '\n[truncated for context budget]'
    if token_bound(messages) > limit:
        raise TaskStopped('context budget exceeded; use /reset or shorten the input')
    return True


def completion(client, model, messages, registry, task):
    settings = task.settings
    definitions = registry.get_definitions()
    available = settings.context_tokens - settings.output_tokens - token_bound(definitions) - 256
    if available <= 0:
        raise TaskStopped('tool definitions exceed context budget')
    if compact(messages, available):
        event(settings, task, 'context_compacted', messages=len(messages))
    for attempt in range(settings.retries + 1):
        task.check()
        input_bound = token_bound(messages) + token_bound(definitions) + 256
        reservation = task.budget.reserve(input_bound, settings.output_tokens)
        start = time.monotonic()
        try:
            timeout = min(settings.api_timeout, task.deadline - start)
            scoped = client.with_options(timeout=timeout, max_retries=0) if hasattr(client, 'with_options') else client
            response = scoped.chat.completions.create(model=model, messages=messages,
                                                     max_tokens=settings.output_tokens,
                                                     **({"tools": definitions} if definitions else {}))
            usage = getattr(response, 'usage', None)
            task.budget.settle(reservation, usage)
            event(settings, task, 'model_response', attempt=attempt, duration=time.monotonic()-start,
                  prompt_tokens=getattr(usage, 'prompt_tokens', None),
                  completion_tokens=getattr(usage, 'completion_tokens', None),
                  charged_tokens=task.budget.tokens, estimated_cost_usd=task.budget.cost if settings.input_usd_per_million or settings.output_usd_per_million else None)
            task.check()
            return response
        except (APIConnectionError, APITimeoutError, APIStatusError) as exc:
            status = getattr(exc, 'status_code', None)
            transient = isinstance(exc, (APIConnectionError, APITimeoutError)) or status in (408, 409, 429) or (status is not None and status >= 500)
            event(settings, task, 'model_error', attempt=attempt, status=status, error_type=type(exc).__name__)
            if not transient or attempt >= settings.retries:
                raise
            # Never exceed task deadline; cap server-provided backoff.
            response = getattr(exc, 'response', None)
            header = response.headers.get('retry-after') if response is not None else None
            try:
                delay = min(10.0, max(0.0, float(header))) if header else min(4.0, 0.5 * 2**attempt)
                if not math.isfinite(delay):
                    delay = 0.5
            except ValueError:
                delay = min(4.0, 0.5 * 2**attempt)
            task.wait(delay)


def tool_results(message, registry, task, checkpoint=None):
    results = []
    interrupted = False
    try:
        for call in message.tool_calls or []:
            start = time.monotonic()
            try:
                task.check()
                if task.pending_reviews:
                    output = 'Error: batch paused for operator review; this operation was not executed.'
                else:
                    output = registry.execute_json(call.function.name, call.function.arguments)
            except ReviewRequired as exc:
                task.require_review(exc.request_id)
                output = f'Error: operator review required ({exc.request_id}). No operation executed. Wait for an operator decision and resubmit the exact operation.'
            except (TaskStopped, KeyboardInterrupt):
                task.stop.set()
                interrupted = True
                output = 'Error: task cancelled or deadline exceeded; tool was not completed. Inspect side effects before retrying.'
            result = {'role': 'tool', 'tool_call_id': call.id, 'content': output[:task.settings.shell_output_bytes]}
            results.append(result)
            if checkpoint:
                checkpoint(result)
            registry.after_result()
            event(task.settings, task, 'tool_result', tool=call.function.name,
                  duration=time.monotonic()-start, failed=output.startswith('Error'))
    finally:
        if interrupted:
            task.stop.set()
    return results
