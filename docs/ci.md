# CI checks and main-branch merge gate

The `Tests` workflow runs on pull requests, main pushes, merge groups and manual
requests. Permissions default to read-only; no model credentials are needed for
required checks. Superseded runs are cancelled and jobs have explicit timeouts.

| Check | Requirement |
| --- | --- |
| Ruff | `E9`/`F` correctness checks, including unused names and undefined variables |
| Test matrix | Ubuntu/Windows with Python 3.10, 3.12 and 3.13 |
| Coverage | Branch coverage ≥80% overall; ≥85% across execution, review, messaging, teammates and file writing |
| Dependency audit | No known vulnerabilities in any exact runtime dependency from `requirements.lock` |
| Docker isolation | Real container isolation and cancellation checks |
| CI gate | Every required job must succeed; failed, cancelled or skipped jobs block the gate |

The live-model job remains manually triggered and separate from the required gate.
It needs the existing `model-integration` environment and `MODEL_API_KEY` secret.
Do not expose model credentials to pull-request jobs.

Coverage includes Python subprocesses. XML and HTML reports are uploaded as
`coverage-reports`, and security results as `runtime-dependency-audit`, with 14-day
retention. The audit also runs weekly to catch newly published vulnerabilities.
Audit failures and collection/network errors fail the check; there are no blanket
vulnerability exclusions. CI-tool packages are separate from the runtime lock;
the dependency audit covers the runtime lock, not all transitive CI tooling.

## Reproduce locally

```bash
python -m pip install -r requirements.lock -r requirements-ci.txt
python -m ruff check .
python -m coverage erase
python -m coverage run -m unittest discover -s tests -v
python -m coverage combine
python -m coverage report --fail-under=80
python -m coverage report --include="agent_runtime/execution.py,agent_runtime/review.py,agent_runtime/messages.py,tools/team.py,tools/write_file.py" --fail-under=85
python -m coverage xml
python -m coverage html
python -m pip_audit --strict -r requirements.lock --no-deps --disable-pip
```

Docker and live-model checks retain their explicit opt-in settings documented in
[README](../README.md). Ruff and coverage configuration live in [pyproject.toml](../pyproject.toml).
The critical-path coverage threshold is aggregate branch coverage, not a promise
that every individual file reaches 85%. Raise thresholds as coverage improves;
do not lower them merely to bypass a regression.

## Activate the repository rule

Adding workflow files does **not** configure GitHub branch protection. Wait for the
new workflow to finish and confirm its `CI gate` check succeeds. A repository admin
can then import [.github/main-ruleset.json](../.github/main-ruleset.json) under
**Settings → Rules → Rulesets**, or create it with:

```bash
gh api repos/crouchggj/EasyAgentic/rulesets --method POST --input .github/main-ruleset.json
```

Inspect existing rulesets first; if `Main CI gate` already exists, update that rule
rather than creating a duplicate. The provided rule is additive and does not replace
other repository protection settings. It requires pull requests and an up-to-date
`CI gate` check, blocks branch deletion and force pushes, and grants no bypass actors.
It does not require another reviewer's approval, so solo maintainers can use it.
A successful Git push does not establish permission to edit these administration
settings. If GitHub rejects the API request, a repository admin must apply the rule
through repository settings with suitable access and a plan that supports rulesets.

After activation, use pull requests for changes to main. No automatic deployment,
PyPI publication, or container release is configured by this change.
