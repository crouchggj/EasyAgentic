"""Application composition and resource ownership, without terminal interaction."""
from dataclasses import dataclass, field
import os
from pathlib import Path

from agent_runtime.client import CancellableClient
from agent_runtime.config import Settings
from agent_runtime.journal import TaskJournal
from agent_runtime.messages import MessageBus
from agent_runtime.review import ReviewQueue, review_queue
from agent_runtime.storage import SessionStore
from tools.registry import setup_registry
from tools.skills import SkillLoader
from tools.todo import TodoManager
from .agents.lead import AgentLoop
from .agents.subagent import SubAgent
from .agents.teammates import TeammateManager


@dataclass
class Application:
    client: object
    agent: AgentLoop
    team_manager: TeammateManager
    skills: SkillLoader
    journal: TaskJournal
    reviews: ReviewQueue
    owns_client: bool = True
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.team_manager.shutdown_all()
        finally:
            if self.owns_client:
                self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def create_application(settings=None, *, client=None, api_key=None, base_url=None,
                       skills_dir=None, team_dir=None):
    """Build the production stack; an injected client remains owned by its caller."""
    settings = settings or Settings.from_env()
    owns_client = client is None
    if owns_client:
        key = api_key or os.environ.get('OPENAI_API_KEY') or os.environ.get('EASYAGENTIC_API_KEY')
        if not key:
            raise SystemExit('Set OPENAI_API_KEY (or EASYAGENTIC_API_KEY) securely before starting.')
        client = CancellableClient(
            api_key=key,
            base_url=base_url if base_url is not None else os.environ.get('OPENAI_URL', 'https://api.openai.com/v1'),
            timeout=settings.api_timeout, max_retries=0,
        )
    manager = None
    try:
        todo = TodoManager()
        subagent = SubAgent(client, settings.model, settings=settings)
        skills = SkillLoader(skills_dir if skills_dir is not None else os.environ.get('SKILLS_DIR', 'skills'))
        directory = Path(team_dir if team_dir is not None else os.environ.get('TEAM_DIR', str(settings.state_path/'team')))
        bus = MessageBus(directory, settings.message_lease, settings.message_attempts)
        manager = TeammateManager(directory, client, settings.model, bus, settings=settings)
        registry = setup_registry(todo, subagent_runner=subagent.run, skill_loader=skills,
                                  team_manager=manager, bus=bus, agent_name='lead')
        agent = AgentLoop(client, registry, todo, subagent, skills, bus, manager,
                          settings=settings, store=SessionStore(settings.state_path/'session.json'))
        journal = TaskJournal(settings.state_path)
        journal.recover()
        reviews = review_queue(settings)
        return Application(client, agent, manager, skills, journal, reviews, owns_client)
    except BaseException:
        try:
            if manager is not None:
                manager.shutdown_all()
        finally:
            if owns_client:
                client.close()
        raise
