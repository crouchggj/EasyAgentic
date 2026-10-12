# EasyAgentic

An experimental command-line agent inspired by
[shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code).
It uses an OpenAI-compatible Chat Completions endpoint, tool execution, synchronous
subagents, Markdown skills and background teammates.

## Setup and run

Python 3.10+ is required. Inbox locking supports Unix and Windows. Docker shell
execution requires a local Docker daemon and a pre-pulled image containing `/bin/sh`.

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
python -m pip check
# Supply OPENAI_API_KEY or EASYAGENTIC_API_KEY securely through your environment manager.
python main.py
```

`requirements.lock` pins the complete tested dependency set. To upgrade, use a fresh
virtual environment, deliberately update the direct version in `requirements.txt`,
resolve dependencies, regenerate the lock and run the tests before committing.
`.env.example` documents environment settings; it is not automatically loaded.
Never commit credentials. `OPENAI_URL` defaults to `https://api.openai.com/v1`.
`MODEL` and `WORKDIR` are compatibility aliases for the corresponding settings.
`SKILLS_DIR` selects skills; `TEAM_DIR` overrides the default state-directory/team path.

## Task controls

| Command | Behavior |
| --- | --- |
| `quit`, `exit`, `q`, or EOF | Cancel teammates, stop their shell processes, close the model client |
| Ctrl-C during a task | Cancel that task and its descendants; keep the checkpoint |
| `/reset` | Stop teammates, drain lead inbox, clear conversation and Todo state |
| `/resume` | Restore the lead conversation and Todo checkpoint |
| `/continue` | Start an explicitly authorized continuation with a new budget |
| `/team-resume NAME` | Resume a stopped teammate from its checkpoint with a new budget |

The lead exposes `bash`, `read_file`, `todo`, `task`, `skill`, `spawn`, `send`,
`read_inbox`, `team_status`, `team_wait`, `team_cancel`, and `team_resume`.
`team_status` includes task IDs, outcomes and retained results; `team_wait` waits
within a bounded timeout without draining the inbox. The lead automatically waits
for working teammates before finalizing and asks the model to incorporate delivered
results. If the wait times out, it labels teammates as pending rather than completed.
Teammates go idle without making model requests, and resume only on new messages.

## Budgets and context

All settings below use the `EASYAGENTIC_` prefix:

| Setting | Default | Purpose |
| --- | --- | --- |
| `MODEL` | `gpt-4o` | Model identifier |
| `MAX_ROUNDS` / `SUBAGENT_ROUNDS` | `50` / `30` | Model rounds per task/cycle |
| `MAX_TOKENS` | `200000` | Total input/output reservation budget, including retries |
| `OUTPUT_TOKENS` | `2048` | Maximum requested response tokens |
| `CONTEXT_TOKENS` | `16000` | Input, tools and response context budget |
| `TASK_TIMEOUT` / `API_TIMEOUT` | `300` / `30` seconds | Task deadline and per-request network timeout |
| `RETRIES` | `2` | Additional attempts for network failures, 408/409/429 and 5xx |
| `MAX_COST_USD` | `0` | Optional estimated cost limit; zero disables it |
| `INPUT_USD_PER_MILLION` / `OUTPUT_USD_PER_MILLION` | `0` / `0` | Operator-supplied provider prices |
| `TEAM_WAIT_TIMEOUT` / `MAX_TEAMMATES` | `30` seconds / `8` | Waiting and concurrency limits |
| `WORKDIR` / `STATE_DIR` | `.` / `.easyagentic` | Workspace and private runtime state |

Root tasks and their spawned/synchronous descendants share a thread-safe token/cost
ledger, deadline and cancellation signal. Messages sent within the same process
carry their task context to an idle teammate. Explicit continuation or resume starts
a new budget; configured limits are per task, not a lifetime billing cap.

Before sending a request, the runtime conservatively reserves UTF-8 byte bounds for
input/tools plus maximum output. Successful usage reports settle the reservation;
failed/unknown requests retain it. This can stop earlier than actual tokenizer-based
accounting. Cost limits require both explicit price settings and apply only to the
configured token prices; they are not a provider billing guarantee. Confirm current
rates and model context capacity with your provider.

Context compaction retains system instructions, the current request, recent complete
tool exchanges and bounded excerpts of earlier conversation. It truncates oversized
tool outputs without separating calls from results. Excerpts can lose detail; a
single oversized request fails with a reset/shorten instruction. The runtime does not
silently drop the current user objective.

The production model client uses the asynchronous SDK internally, so cancellation
and task deadlines cancel an in-flight request without closing other agents'
connections. Authentication/parameter failures are not retried. Transient failures
use capped backoff within the remaining deadline, with SDK automatic retries disabled.

## Shell policy

`EASYAGENTIC_SHELL_MODE=disabled` is the default. The model cannot enable shell access.
An operator can choose `docker`, or explicitly choose unsafe `local` mode on Unix.
Local mode sets cwd and strips credential variables but is **not** filesystem or
network isolation. Windows local mode is disabled because reliable descendant
cleanup requires Docker.

For Docker, pull and review an image, obtain its repository digest, then configure it:

```bash
docker pull alpine:3.22
docker image inspect alpine:3.22 --format '{{index .RepoDigests 0}}'
# Set EASYAGENTIC_SHELL_MODE=docker and EASYAGENTIC_DOCKER_IMAGE to that full digest.
```

Execution uses `--pull=never`, no network, a read-only container root, all capabilities
dropped, no privilege escalation, CPU/memory/PID limits, bounded tmpfs and no Docker
socket mount. Only the configured workdir is mounted read-write; its existing files
are therefore accessible to commands. Do not place credentials or host-sensitive
files in that workdir. The container runs as the host UID/GID on Unix.
Proxy variables are cleared and application credentials are not passed to the container.

`SHELL_TIMEOUT` defaults to 30 seconds, and `SHELL_OUTPUT_BYTES` to 64000. The output
reader keeps only a bounded buffer and stops excessive output. Timeout/cancellation
kills the Unix process group or removes the Docker container; background children are
cleaned up even when the original shell exits. Shell commands have no network in Docker
mode; install needed tools into the reviewed image beforehand. These restrictions can
make some repository operations unavailable until the operator changes policy.

## State, recovery and observability

`.easyagentic/session.json` stores lead messages and Todo state. Team checkpoints
contain teammate messages, Todo state and progress; member configuration retains
prompt, role, task ID, outcome and result. Writes use private temporary files and
atomic replacement. File locks protect inbox operations across processes and
configuration writes. Run one teammate manager per team directory.

After restart, members are marked shutdown; `/team-resume` or `team_resume` explicitly
restores them. Completed tool outputs are retained. Incomplete tool calls receive an
interruption marker instead of being automatically rerun: external effects may have
happened before a crash, so inspect them before retrying. Arbitrary shell operations
are not transactional, and this mechanism cannot guarantee exactly-once effects.
Inbox consumption is atomic under its lock, but a process crash between consumption
and checkpointing can lose an in-memory delivery. Retained teammate results remain
available through `team_status`.

`events.jsonl` records task/parent IDs, model/tool latency, retry status, usage and
configured cost estimates. It omits prompts, tool arguments/results, credential
values and API error bodies. Session checkpoints intentionally contain conversation
and tool results, which may be sensitive; protect the state directory and rotate or
remove state/log files according to your retention needs. Runtime files are ignored
by Git. Checkpoints are versioned and are not executable configuration.

## Validation and CI

```bash
python -m unittest discover -s tests -v
```

Default tests require neither credentials nor Docker. They cover budgets, protocol
preservation, compaction, durable recovery, concurrent messaging, teammate controls,
shell policy and real SDK calls against a local HTTP fixture, including cancellation
and 429 retries. Windows skips Unix local-shell checks. CI runs Python 3.10, 3.12 and
3.13 on Ubuntu and Windows, plus a separate Docker isolation job on Ubuntu.

Optional real Docker checks (pre-pull the image and provide its full repository digest):

```bash
EASYAGENTIC_TEST_DOCKER_IMAGE='alpine@sha256:YOUR_REVIEWED_DIGEST' \
  python -m unittest discover -s tests -p test_docker.py -v
```

Optional live model validation requires explicit opt-in and securely configured
credentials. It makes a small billable request and fails if opted in without a key:

```bash
EASYAGENTIC_RUN_LIVE=1 python -m unittest discover -s tests -p test_live.py -v
```

The manually dispatched `live-model` CI job reads `MODEL_API_KEY` from the
`model-integration` GitHub environment; optional `MODEL` environment variable selects
the model. Configure that environment before running it. Skipped live/Docker checks
are not evidence of those integrations working.
