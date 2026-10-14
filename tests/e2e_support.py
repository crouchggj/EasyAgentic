"""Build the production stack for local and opt-in live workflow validation."""
from pathlib import Path
from easyagentic.bootstrap import create_application

WORKFLOW_QUERY = (
    'Read README.md to analyze this repository. Spawn exactly one teammate named reviewer, '
    'with role architecture reviewer, and ask it to read README.md and summarize the architecture. '
    'Use team_wait to collect the reviewer result. In your final answer, incorporate that result '
    'and include the marker E2E_READY. Do not use shell commands or modify any files.'
)


def build_agent(client, settings):
    application = create_application(
        settings, client=client,
        skills_dir=Path(__file__).resolve().parents[1]/'skills',
        team_dir=settings.state_path/'team',
    )
    return application.agent, application.team_manager
