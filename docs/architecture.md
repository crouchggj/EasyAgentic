# Architecture and incremental refactoring

The first refactoring phase separates terminal interaction, application assembly,
and agent orchestration. Existing commands, review requirements, checkpoints,
and message delivery semantics remain in place.

```text
main.py / python -m easyagentic
  -> easyagentic.cli          terminal I/O and error presentation
     -> easyagentic.commands operator commands and continuation routing
     -> easyagentic.bootstrap application assembly and resource ownership
        -> easyagentic.agents.lead / subagent / teammates
           -> tools.registry and tools.collaboration
           -> agent_runtime execution, review, messaging and persistence
```

`create_application()` builds the same stack used by the CLI and end-to-end
workflow tests. Its returned `Application` supports a context manager, shuts down
teammates, and closes the model client it creates. An injected client remains
owned by the caller. `run_cli()` accepts input/output functions and does not own
application lifetime. `CommandRouter` handles operator commands without calling
the model; approval records a decision and requires explicit continuation for
lead execution.

The canonical agent implementations live in `easyagentic/agents/`. Imports from
`main.AgentLoop`, `tools.subagent`, `tools.team`, and the existing public exports
of `tools` remain supported. The `tools` package resolves compatibility exports
lazily so importing a canonical agent does not create circular imports or start
an application. New code should import implementations directly from their
canonical modules. Coverage gates measure those implementations, including the
moved teammate manager.

Both `python main.py` and `python -m easyagentic` run from the repository root.
This phase does not introduce an installed distribution or console script.

## Following phases

Keep future changes independently reviewable:

1. Extract common model/tool iteration mechanics only after documenting the
   distinct lifecycle and cancellation rules for lead, child, and teammate agents.
2. Separate persistence adapters from session, review, journal, and delivery
   policies while retaining existing state formats and crash-recovery tests.
3. Organize tests into unit, integration, and optional live suites; update discovery
   and CI together so no existing checks disappear.
4. Add package metadata and an installed CLI once resource paths and external
   `agent_runtime` / `tools` packages have explicit installation contracts.

Avoid combining these phases with storage migrations or changes to operator
review policy. Each phase should preserve the CLI and import compatibility until
an explicit deprecation is documented.

## Learning-oriented cleanup

The [learning guide](learning.md) introduces a standalone, read-only tool loop in
`examples/basic_agent.py` before the full agent stack. It reuses the bounded file
reader and exposes no write or shell tool. Application and example code are both
included in overall coverage. Regression tests use canonical module imports;
`tests/test_application.py` separately verifies legacy import compatibility.

Unused terminal tool-formatting helpers and their compatibility exports were
removed; remaining compatibility entry points are retained. Detailed operational
instructions moved from README to [the application reference](operations.md).
