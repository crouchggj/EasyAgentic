# Learning Agentic step by step

Install dependencies and configure credentials using [README](../README.md#quick-start).
Run all commands from the repository root. The source and its matching tests are
intended to be read together.

## 1. Understand the tool loop

Read [examples/basic_agent.py](../examples/basic_agent.py), then run:

```bash
python -m examples.basic_agent "Read README.md and explain this project."
```

Follow the `messages` list through one iteration:

1. Send the system instruction, user request, and `read_file` schema to the model.
2. Append the assistant response, including any tool calls.
3. Parse each call's JSON arguments and execute the allowed read handler.
4. Append a `tool` message using the matching `tool_call_id`.
5. Ask the model again; stop when it answers without tool calls or reaches the limit.

The model proposes a tool call; Python executes it. Returning file text to the model
is another message, not a new user request. A response may contain several calls,
so every call needs its own result, including invalid calls.

The example reuses [bounded workspace reading](../tools/read_file.py). By default,
its root is the current directory; `EASYAGENTIC_WORKDIR` can set another root. It
resolves paths and limits returned bytes. It has no shell or file-writing tool.
Use a learning workspace whose files you intend to send to your configured provider.
The lesson has a round limit and API timeout; shared budgets, checkpoints, retries,
and operator commands belong to the full application.

Study the offline [example tests](../tests/test_basic_example.py) for a deterministic
model/tool exchange, invalid calls, path restrictions, and the round limit:

```bash
python -m unittest discover -s tests -p test_basic_example.py -v
```

## 2. Understand synchronous delegation

Read [SubAgent](../easyagentic/agents/subagent.py), then
[the task adapter](../tools/task.py). A parent delegates a bounded subtask to an
agent with its own conversation and a limited tool registry, then receives a
summary. The child cannot recursively delegate. Compare its short loop with the
lesson before studying the shared `completion()` and `tool_results()` helpers in
[execution.py](../agent_runtime/execution.py).

## 3. Understand background collaboration

Read [collaboration tool schemas](../tools/collaboration.py), followed by
[TeammateManager](../easyagentic/agents/teammates.py). Trace one spawn, message,
and result collection. Next read [the lead loop](../easyagentic/agents/lead.py)
to see why it waits for pending teammates before returning a final answer.

The teammate manager also contains checkpoint and delivery recovery. On a first
pass, follow normal execution; revisit failure handling in the next stage.
[End-to-end tests](../tests/test_e2e.py) demonstrate read, delegate, wait, and summarize
without contacting a live model.

## 4. Understand reliability and application assembly

Read [bootstrap.py](../easyagentic/bootstrap.py) for resource ownership,
[commands.py](../easyagentic/commands.py) for operator decisions, and
[the architecture guide](architecture.md) for module boundaries.

Then follow these concerns separately:

- Execution: `agent_runtime/execution.py` and `tests/test_runtime.py`.
- Human review: `agent_runtime/review.py` and `tests/test_review.py`.
- Durable messaging: `agent_runtime/messages.py` and `tests/test_messages.py`.
- Session recovery: `agent_runtime/storage.py` and the agent checkpoint methods.

Use [the application reference](operations.md) for configuration, shell policy,
recovery commands, and optional integration checks. Keep these mechanisms intact
when experimenting with the full application.

## Compatibility and cleanup

New code imports concrete modules directly. `main.py`, `tools/team.py`,
`tools/subagent.py`, and the remaining lazy `tools` exports retain legacy entry
points; dedicated compatibility tests cover them. Unused terminal tool-formatting
helpers (`tool_header`, `tool_args`, `tool_output`, and `TOOL_COLORS`) were removed
from both implementation and exports. `colorize` and `round_header` remain in use.
