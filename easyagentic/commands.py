"""Operator command routing; no input loop, model calls or resource construction."""
from dataclasses import dataclass
import json


@dataclass(frozen=True)
class CommandResult:
    handled: bool
    output: str | None = None
    exit_requested: bool = False
    query: str | None = None


class CommandRouter:
    def __init__(self, application):
        self.application = application

    def handle(self, query):
        app = self.application
        agent = app.agent
        manager = app.team_manager
        reviews = app.reviews
        bus = agent.bus
        if not query:
            return CommandResult(True)
        if query.lower() in ('quit', 'exit', 'q'):
            return CommandResult(True, 'Goodbye!', exit_requested=True)
        if query == '/tasks':
            return CommandResult(True, json.dumps(app.journal.list(), indent=2))
        if query == '/reviews':
            output = '\n'.join(f"{r['id']} {r['status']} {r['operation']['tool']}" for r in reviews.requests())
            return CommandResult(True, output or 'No review requests')
        if query.startswith('/review '):
            return CommandResult(True, reviews.preview(query.split(maxsplit=1)[1]))
        if query.startswith(('/approve ', '/reject ')):
            request = reviews.decide(query.split(maxsplit=1)[1], query.startswith('/approve '))
            agent.messages.append({'role': 'user', 'content': f"Operator {request['status']} review {request['id']}. Resubmit only the exact approved operation."})
            agent.checkpoint('review_decided')
            manager.notify_review(request)
            return CommandResult(True, request['status'] + '; use /continue for lead work')
        if query == '/messages':
            return CommandResult(True, json.dumps(bus.status(), indent=2))
        if query.startswith('/message-retry '):
            return CommandResult(True, bus.retry(query.split(maxsplit=1)[1]))
        if query == '/reset':
            manager.shutdown_all()
            bus.discard('lead')
            agent.reset()
            return CommandResult(True, 'Session reset')
        if query == '/resume':
            return CommandResult(True, agent.restore())
        if query.startswith('/team-resume '):
            return CommandResult(True, manager.resume(query.split(maxsplit=1)[1]))
        if query == '/continue':
            query = 'Continue the saved task; inspect existing results and do not repeat completed actions.'
        return CommandResult(False, query=query)
