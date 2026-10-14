# Full application reference

Run the commands below from the repository root. For the learning sequence, see
[the learning guide](learning.md); for installation, see [README](../README.md).

## Interactive commands

| Command | Effect |
| --- | --- |
| `quit`, `exit`, `q`, or EOF | Cancel teammates, clean up shell processes, and close the model client |
| Ctrl-C during a task | Cancel the task and its descendants while retaining the checkpoint |
| `/reset` | Stop teammates, drain the lead inbox, and clear lead conversation and Todo state |
| `/resume` | Restore the saved lead conversation and Todos |
| `/continue` | Continue the current in-memory conversation with a new task budget |
| `/team-resume NAME` | Restore a stopped teammate; completed checkpoints republish retained results without rerunning the model |
| `/reviews`, `/review ID` | List requests and inspect the exact diff or shell command |
| `/approve ID`, `/reject ID` | Decide a previewed request; only the operator can issue these commands |
| `/tasks` | Inspect durable task outcomes, including interrupted work |
| `/messages`, `/message-retry ID` | Inspect delivery state and explicitly requeue a dead-letter message |

After restarting the application, use `/resume` followed by `/continue` to continue a
saved lead task. Teammate threads do not restart automatically.

## Execution review

Every `write_file` edit and enabled `bash` command requires a separate operator decision.
The agent pauses without executing the operation or later calls in that tool batch:

```text
/reviews
/review REQUEST_ID
/approve REQUEST_ID
/continue
```

Use `/reject REQUEST_ID` to decline. Approval alone does not execute lead work; the
agent must resubmit the identical operation after `/continue`. Awaiting teammates
receive the decision and resume their workflow. Editing the proposal, changing the
file's reviewed base, or changing shell mode/image requires a fresh review. Grants
are one-use, expire after `REVIEW_TIMEOUT`, and do not survive process restarts;
persisted request files are never treated as authorization. Preview again before
reapproving a request after restart. No approval tool is exposed to the model.

File previews show unified diffs. The file tool cannot edit outside the workspace,
Git metadata, or runtime state. Shell previews show the full command, workdir and
mode; arbitrary shell effects cannot be predicted as a file diff. An approved shell
command still has the permissions of the configured executor. Review complements
Docker isolation, rather than replacing it. Requests contain command text and file
contents: protect their state directory along with checkpoints.

## Configuration

The settings in this table use the **`EASYAGENTIC_` prefix**. For example,
`MAX_ROUNDS` means `EASYAGENTIC_MAX_ROUNDS`.

| Setting | Default | Controls |
| --- | --- | --- |
| `MODEL` | `gpt-4o` | Model identifier |
| `MAX_ROUNDS` / `SUBAGENT_ROUNDS` | `50` / `30` | Model rounds per lead/teammate cycle and synchronous subtask |
| `MAX_TOKENS` | `200000` | Shared input/output reservation budget, including retries |
| `OUTPUT_TOKENS` / `CONTEXT_TOKENS` | `2048` / `16000` | Response limit and total request context budget |
| `TASK_TIMEOUT` / `API_TIMEOUT` | `300` / `30` seconds | Task deadline and per-request network timeout |
| `RETRIES` | `2` | Additional attempts for transient API failures |
| `MAX_COST_USD` | `0` | Estimated cost limit; zero disables it |
| `INPUT_USD_PER_MILLION` / `OUTPUT_USD_PER_MILLION` | `0` / `0` | Provider token prices used for cost accounting |
| `TEAM_WAIT_TIMEOUT` / `MAX_TEAMMATES` | `30` seconds / `8` | Waiting and teammate concurrency limits |
| `WORKDIR` / `STATE_DIR` | `.` / `.easyagentic` | Workspace root and runtime state directory |
| `SHELL_MODE` | `disabled` | `disabled`, `docker`, or Unix-only `local` |
| `SHELL_TIMEOUT` / `SHELL_OUTPUT_BYTES` | `30` seconds / `64000` bytes | Shell timeout and bounded output; byte limit also bounds file reads |
| `DOCKER_IMAGE` | Empty | Pre-pulled shell image pinned by repository digest |
| `REVIEW_TIMEOUT` / `WRITE_LIMIT_BYTES` | `900` seconds / `1048576` bytes | Review expiry and maximum editable text file size |
| `MESSAGE_LEASE` / `MESSAGE_ATTEMPTS` | `30` seconds / `5` | Unacknowledged delivery lease and retry limit |

Additional variables have **no prefix**: `OPENAI_URL` selects the API base URL,
`SKILLS_DIR` selects skills, and `TEAM_DIR` overrides the team state path.
`MODEL` and `WORKDIR` are compatibility aliases; their `EASYAGENTIC_` forms take precedence.

### Budget and context behavior

A root task and its synchronous/spawned descendants share a token/cost ledger,
deadline, and cancellation signal. In-process messages carry their task context to
an idle teammate. Explicit continuation or resume starts a new budget: these limits
are per task, not a lifetime spending cap.

Input is conservatively estimated using UTF-8 byte bounds, with maximum output
reserved before each request. Successful API usage reports settle the reservation;
failed or unknown requests retain it. Limits may therefore stop work earlier than
actual tokenizer accounting would. Cost limits require both price settings and depend
on the rates you supply; they are not a provider billing guarantee.

Context compaction preserves system instructions, the current request, and recent
complete tool exchanges, adding bounded excerpts of older conversation. Older detail
can be lost. Oversized tool outputs are truncated; an oversized user request prompts
you to reset or shorten it. Set limits to suit your provider's model capacity.

Transient connection failures and HTTP 408/409/429/5xx responses use bounded backoff.
Authentication and parameter errors are not retried. Task cancellation can interrupt
an in-flight request without closing other agents' connections.

## Enable shell execution

The operator chooses the policy; the model cannot enable shell access.

### Docker isolation

Requires a local Docker daemon and a reviewed image containing `/bin/sh`. On Unix,
the executor uses `/var/run/docker.sock`.

```bash
docker pull alpine:3.22
export EASYAGENTIC_DOCKER_IMAGE="$(docker image inspect alpine:3.22 --format '{{index .RepoDigests 0}}')"
export EASYAGENTIC_SHELL_MODE=docker
python main.py
```

The executor requires a `repository@sha256:...` digest and uses `--pull=never`.
Containers have no network, a read-only root filesystem, dropped capabilities,
CPU/memory/PID limits, bounded temporary storage, and no mounted Docker socket.
Only the configured workdir is mounted read-write. On Unix, commands use the host UID/GID.
Application credentials are not passed through, and proxy variables are cleared.

Commands can access **all files in the mounted workdir**. Keep credentials and sensitive
host files outside it. Install required tools into the reviewed image beforehand;
network-dependent commands will not work inside this sandbox. Alpine is a minimal
example, not a complete Python development image.

### Local execution

On Unix, `EASYAGENTIC_SHELL_MODE=local` explicitly enables commands with the application's
OS permissions. It sets the working directory and strips credential variables, but
provides **no filesystem or network isolation**. Windows local mode is disabled; use Docker.

Both modes bound output and clean up process trees or containers on timeout and
cancellation, including background children whose original shell has exited.

## Checkpoints and logs

Default runtime files live under `.easyagentic/` and are ignored by Git:

| Path | Contents |
| --- | --- |
| `session.json` | Lead conversation, Todos, and checkpoint status |
| `team/config.json` | Member roles, prompts, task IDs, outcomes, and retained results |
| `team/tasks/` | Teammate conversation and Todo checkpoints |
| `team/inbox/messages.sqlite3` | Durable message queue, leases, acknowledgements and dead letters |
| `tasks.sqlite3` | Task/parent IDs, outcomes and interruption records |
| `reviews/` | Proposed operations and operator decisions |
| `events.jsonl` | Task/parent IDs, latency, retry status, usage, and configured cost estimates |

Checkpoints use atomic replacement; inbox operations use portable file locks.
Run **one CLI process per state directory and one teammate manager per team directory**. Restoration retains completed tool
results and marks incomplete calls as interrupted instead of automatically rerunning
them. Inspect interrupted actions before retrying: side effects may already have occurred.

Messages have stable IDs, leased delivery and explicit acknowledgement. Agent loops
save received IDs with their conversation checkpoint **before** acknowledging. A crash
before saving permits redelivery; a crash after saving is deduplicated against those
IDs, even after context compaction. The `read_inbox` tool acknowledges only after its
tool result is saved. `send` accepts an optional `message_id` for idempotent retries;
reusing an ID with different content or recipient is rejected.

Failed deliveries are retried up to `MESSAGE_ATTEMPTS`, then retained as dead letters.
Inspect `/messages` and use `/message-retry ID` after fixing the cause. Completed teammate
checkpoints retain an outbox: restoring them republishes the same result ID without
rerunning the model. Legacy JSONL inboxes migrate automatically and remain as
`.jsonl.migrated` backups. The low-level `MessageBus.read_inbox()` convenience method
acknowledges immediately; use the checkpoint-aware `receive()` API for durable consumers.

`/tasks` marks previously running/waiting tasks as interrupted on application restart.
Task records and message acknowledgements do not make arbitrary shell effects
transactional: inspect interrupted actions before retrying them. Queue history and
deduplication IDs are retained; manage their storage together with checkpoint retention.

Event logs omit prompts, tool arguments/results, credentials, and API error bodies.
Checkpoints contain conversation and tool results, which may be sensitive. Protect
the state directory and manage its retention; logs are not automatically rotated.

## Tests and CI

Run the default suite without API credentials or Docker:

```bash
python -m pip check
python -m unittest discover -s tests -v
```

Coverage includes budgets, tool-call protocol, context compaction, recovery, concurrent
messaging, teammate controls, shell policy, and real SDK requests against a local HTTP
fixture, including cancellation and 429 retries. The default end-to-end test exercises
the complete read → delegate → wait → summarize flow through the SDK against a local
HTTP fixture; review, crash/restart and message redelivery tests require no API key. Windows skips Unix local-shell tests.

For real Docker checks, use a pre-pulled pinned image:

```bash
export EASYAGENTIC_TEST_DOCKER_IMAGE="$EASYAGENTIC_DOCKER_IMAGE"
python -m unittest discover -s tests -p test_docker.py -v
```

For billable live model validation, configure credentials and explicitly opt in. The
suite includes an authentication check and a multi-call repository workflow: read
README, delegate an architecture review, wait for the teammate, and incorporate its
result. It asserts actual tool use and durable outcomes:

```bash
EASYAGENTIC_RUN_LIVE=1 python -m unittest discover -s tests -p test_live.py -v
```

Skipped checks do not establish integration readiness. Opting into the live test
without a credential fails the test.

[GitHub Actions](../.github/workflows/tests.yml) runs Python 3.10, 3.12, and 3.13 on Linux
(Ubuntu), plus Docker isolation on Ubuntu. Windows CI is not enabled. The manually dispatched `live-model`
job requires `MODEL_API_KEY` in the `model-integration` GitHub environment; its optional
`MODEL` variable selects the model.

### Quality and merge checks

CI also runs Ruff correctness checks, branch coverage gates (80% overall and 85%
across critical runtime paths), and a strict audit of the runtime dependency lock.
Coverage and audit reports are available as workflow artifacts. A stable `CI gate`
check aggregates all required jobs; optional live-model checks remain separate.

See [CI configuration and branch protection](ci.md) for local commands, report
retention, weekly security scans, and the ready-to-import main-branch ruleset.
The ruleset must be applied in GitHub settings; committing it does not activate it.

