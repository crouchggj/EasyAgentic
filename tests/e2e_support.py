"""Build the production stack for local and opt-in live workflow validation."""
from pathlib import Path
from agent_runtime.storage import SessionStore
from main import AgentLoop
from tools import MessageBus, SkillLoader, SubAgent, TeammateManager, TodoManager, setup_registry


WORKFLOW_QUERY = (
    'Read README.md to analyze this repository. Spawn exactly one teammate named reviewer, '
    'with role architecture reviewer, and ask it to read README.md and summarize the architecture. '
    'Use team_wait to collect the reviewer result. In your final answer, incorporate that result '
    'and include the marker E2E_READY. Do not use shell commands or modify any files.'
)


def build_agent(client, settings):
    root = Path(settings.workdir)
    state = settings.state_path
    bus = MessageBus(state/'team',settings.message_lease,settings.message_attempts)
    manager = TeammateManager(state/'team',client,settings.model,bus,settings=settings)
    todo = TodoManager()
    skills = SkillLoader(str(Path(__file__).resolve().parents[1]/'skills'))
    child = SubAgent(client,settings.model,settings=settings)
    registry = setup_registry(todo,subagent_runner=child.run,skill_loader=skills,team_manager=manager,bus=bus)
    agent = AgentLoop(client,registry,todo,child,skills,bus,manager,settings=settings,
                      store=SessionStore(state/'session.json'))
    return agent,manager
