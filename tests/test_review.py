import json
from pathlib import Path
import tempfile
import unittest

from agent_runtime.config import Settings
from agent_runtime.execution import Task, task_scope
from agent_runtime.review import ReviewQueue, ReviewRequired, review_queue
from agent_runtime.storage import SessionStore
from easyagentic.agents.lead import AgentLoop
from agent_runtime.messages import MessageBus
from tools.skills import SkillLoader
from easyagentic.agents.subagent import SubAgent
from tools.todo import TodoManager
from tools.registry import setup_registry
from tools.write_file import handle_write_file
from test_agents import Client, message
from types import SimpleNamespace as NS


class ReviewTests(unittest.TestCase):
    def test_file_diff_review_one_use_and_stale_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(workdir=tmp, state_dir=str(Path(tmp)/'state'))
            path = Path(tmp)/'code.txt'
            path.write_text('old\n')
            args = {'path':'code.txt','content':'new\n'}
            queue = review_queue(settings)
            with task_scope(Task(settings)):
                with self.assertRaises(ReviewRequired) as caught:
                    handle_write_file(args)
                identifier = caught.exception.request_id
                self.assertEqual(path.read_text(), 'old\n')
                with self.assertRaises(ValueError):
                    queue.decide(identifier, True)  # A preview is required before approval.
                preview = queue.preview(identifier)
                self.assertIn('-old', preview)
                self.assertIn('+new', preview)
                queue.decide(identifier, True)
                self.assertIn('Updated', handle_write_file(args))
                self.assertEqual(path.read_text(), 'new\n')
                with self.assertRaises(ReviewRequired):
                    handle_write_file(args)  # Approval is one-use, not a blanket grant.
                stale = {'path':'code.txt','content':'next\n'}
                with self.assertRaises(ReviewRequired) as caught:
                    handle_write_file(stale)
                identifier = caught.exception.request_id
                queue.preview(identifier)
                queue.decide(identifier, True)
                path.write_text('external edit\n')
                with self.assertRaises(ReviewRequired):
                    handle_write_file(stale)
                self.assertEqual(path.read_text(), 'external edit\n')

    def test_reject_restart_expiry_and_tampered_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            queue = ReviewQueue(path)
            operation = {'tool':'bash','command':'echo first','mode':'local','workdir':tmp}
            with self.assertRaises(ReviewRequired) as caught:
                queue.require(operation)
            identifier = caught.exception.request_id
            queue.preview(identifier)
            request = queue.get(identifier)
            request['operation']['command'] = 'echo changed'
            request['fingerprint'] = queue.fingerprint(request['operation'])
            (path/f'{identifier}.json').write_text(json.dumps(request))
            with self.assertRaises(ValueError):
                queue.decide(identifier, True)
            queue.preview(identifier)
            queue.decide(identifier, False)
            with self.assertRaises(PermissionError):
                queue.require(request['operation'])
            with self.assertRaises(ReviewRequired) as caught:
                queue.require(operation)
            identifier = caught.exception.request_id
            queue.preview(identifier)
            queue.decide(identifier, True)
            # A persisted approved status does not grant execution after restart.
            with self.assertRaises(ReviewRequired):
                ReviewQueue(path).require(operation)
            request = queue.get(identifier)
            request['expires'] = 0
            (path/f'{identifier}.json').write_text(json.dumps(request))
            with self.assertRaises(ValueError):
                queue.decide(identifier, True)

    def test_agent_pauses_batch_until_review_then_executes_exact_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(workdir=tmp,state_dir=str(Path(tmp)/'state'))
            todo = TodoManager()
            call = NS(id='edit',function=NS(name='write_file',arguments=json.dumps({'path':'new.txt','content':'reviewed\n'})))
            later = NS(id='read',function=NS(name='read_file',arguments='{"path":"new.txt"}'))
            client = Client([message(calls=[call,later]), message(calls=[call]), message('done')])
            registry = setup_registry(todo)
            store = SessionStore(settings.state_path/'session.json')
            agent = AgentLoop(client,registry,todo,None,SkillLoader(),MessageBus(Path(tmp)/'team'),None,settings=settings,store=store)
            result = agent.run('create new.txt')
            self.assertIn('Awaiting operator review', result)
            self.assertFalse((Path(tmp)/'new.txt').exists())
            self.assertEqual(store.load()['status'], 'awaiting_review')
            results = [m for m in agent.messages if m['role']=='tool']
            self.assertEqual(len(results), 2)
            self.assertIn('not executed', results[-1]['content'])
            request = review_queue(settings).requests()[0]
            review_queue(settings).preview(request['id'])
            review_queue(settings).decide(request['id'],True)
            self.assertEqual(agent.run('approved; continue'), 'done')
            self.assertEqual((Path(tmp)/'new.txt').read_text(), 'reviewed\n')
            self.assertFalse(registry.has_tool('approve'))

    def test_paths_are_restricted_and_child_review_reaches_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(workdir=tmp,state_dir=str(Path(tmp)/'state'))
            root = Task(settings)
            with task_scope(root):
                for path in ('../outside', '.git/config', 'state/forged'):
                    with self.assertRaises(ValueError):
                        handle_write_file({'path':path,'content':'bad'})
                call = NS(id='edit',function=NS(name='write_file',arguments='{"path":"new","content":"text"}'))
                child = SubAgent(Client([message(calls=[call])]),'model',settings=settings)
                self.assertIn('Awaiting',child.run('edit file'))
                self.assertEqual(len(root.pending_reviews),1)
                self.assertFalse((Path(tmp)/'new').exists())

    def test_background_review_notifies_operator_and_resumes_after_decision(self):
        import time
        from easyagentic.agents.teammates import TeammateManager
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(workdir=tmp,state_dir=str(root/'state'))
            call = NS(id='edit',function=NS(name='write_file',arguments='{"path":"peer.txt","content":"approved"}'))
            client = Client([message(calls=[call]),message(calls=[call]),message('peer done')])
            bus = MessageBus(root/'team')
            manager = TeammateManager(root/'team',client,'model',bus,settings=settings)
            try:
                manager.spawn('worker','editor','create peer.txt')
                state = json.loads(manager.wait('worker',2))
                self.assertEqual(state['status'],'awaiting_review')
                self.assertFalse((root/'peer.txt').exists())
                queue = review_queue(settings)
                identifier = state['pending_reviews'][0]
                queue.preview(identifier)
                request = queue.decide(identifier,True)
                manager.notify_review(request)
                deadline = time.monotonic()+3
                while manager.list_members()[0].get('outcome') != 'completed' and time.monotonic()<deadline:
                    time.sleep(0.01)
                self.assertEqual(manager.list_members()[0]['outcome'],'completed')
                self.assertEqual((root/'peer.txt').read_text(),'approved')
                self.assertEqual(queue.get(identifier)['status'],'consumed')
            finally:
                manager.shutdown_all()

    def test_shell_preview_escapes_terminal_control_and_bidi_characters(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = ReviewQueue(Path(tmp))
            with self.assertRaises(ReviewRequired) as caught:
                queue.require({'tool':'bash','command':'echo \x1b[2J\u202ehidden','workdir':tmp,'mode':'local'})
            preview = queue.preview(caught.exception.request_id)
            self.assertNotIn('\x1b',preview)
            self.assertNotIn('\u202e',preview)
            self.assertIn('\\u001b',preview)
            self.assertIn('\\u202e',preview)

    def test_lead_does_not_finalize_if_review_arrives_during_team_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Task(Settings(state_dir=tmp))
            class Manager:
                def has_pending(self):
                    return True
                def wait_all(self,timeout):
                    root.require_review('review-from-worker')
            agent = AgentLoop(Client([message('provisional')]),setup_registry(TodoManager()),TodoManager(),None,
                              SkillLoader(),MessageBus(Path(tmp)),Manager(),settings=root.settings)
            with task_scope(root):
                self.assertIn('Awaiting operator review',agent.run('do team work'))
