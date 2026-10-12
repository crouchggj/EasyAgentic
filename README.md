# Easy Agent

This experimental project is from [shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code).

The main purpose is to test the agentic capabilities of large language models.

## Run

Requires Python 3.10+ on a Unix platform (the message bus uses `fcntl` file locks).

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
# Set OPENAI_API_KEY securely in your shell or environment manager.
python main.py
```

Optional environment variables: `OPENAI_URL` (default `https://api.openai.com/v1`),
`MODEL` (default `gpt-4o`), `SKILLS_DIR` (default `skills`), `TEAM_DIR` (default
`.team`), and `WORKDIR` (default current directory). Start from the repository root.
Type `quit`, `exit`, or `q` to exit.

The main agent retains conversation history for the current process and allows
at most 50 model calls per user query. `task` runs a synchronous subagent with a
fresh Todo list and a 30-call limit. `spawn` starts a background teammate with a
50-call limit per work cycle. Teammates report final answers to `lead` and wait
without making model requests until new inbox messages arrive. Main-agent inbox
messages are consumed on its next model round, including a subsequent user query.

Inbox append/drain operations use file locks. Member configuration writes are
serialized within a manager and replaced atomically. Use one teammate manager per
team directory. On restart, recorded members are marked shutdown; threads and
conversation history are not restored. Agent names accept 1–64 ASCII letters,
digits, underscores or hyphens; `lead` is reserved for the main agent.

Tool parameter/handler failures are returned to the model as tool results.
Teammate API failures are reported to `lead`; the teammate waits for new
instructions instead of retrying indefinitely. Quit requests all teammates to
stop, with a bounded wait; an in-flight model request or shell command cannot be
interrupted immediately.

Bash commands run with the application's OS permissions. Run this experimental
agent in an isolated environment. Round limits do not impose a token/cost budget
or compact long conversations.

## Tests

```bash
python -m unittest discover -s tests -v
```

The regression suite uses simulated model responses and requires no API key.
