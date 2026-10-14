import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace as NS

from easyagentic.agents.lead import AgentLoop
from agent_runtime.messages import MessageBus
from tools.skills import SkillLoader
from easyagentic.agents.subagent import SubAgent
from easyagentic.agents.teammates import TeammateManager
from tools.todo import TodoManager
from tools.base import ToolRegistry
from tools.registry import setup_registry


def message(content=None, calls=None):
    return NS(content=content, tool_calls=calls)


def tool_call(identifier, arguments='{}'):
    return NS(id=identifier, function=NS(name='probe', arguments=arguments))


class Client:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kwargs):
        self.requests.append([dict(m) for m in kwargs['messages']])
        return NS(choices=[NS(message=next(self.replies))])


class AgentTests(unittest.TestCase):
    def test_reminder_preserves_results_and_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo = TodoManager()
            todo.update([{'id': '1', 'text': 'active'}])
            client = Client([message(calls=[tool_call(str(i))]) for i in range(3)] +
                            [message('done'), message('follow-up')])
            registry = setup_registry(todo)
            executed = []
            registry.register('probe', {}, lambda args: executed.append(args) or 'ok')
            agent = AgentLoop(client, registry, todo, None, SkillLoader(), MessageBus(Path(tmp)), None)
            self.assertEqual(agent.run('first question'), 'done')
            self.assertEqual(len(executed), 3)
            history = client.requests[3]
            self.assertEqual([m['tool_call_id'] for m in history if m['role'] == 'tool'], ['0', '1', '2'])
            self.assertIn('REMINDER', history[-1]['content'])
            self.assertEqual(agent.run('second question'), 'follow-up')
            self.assertTrue(any(m.get('content') == 'first question' for m in client.requests[-1]))

    def test_bad_arguments_and_handler_errors_return_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Client([message(calls=[tool_call('bad', '{'), tool_call('array', '[]'), tool_call('raises')]), message('recovered')])
            registry = ToolRegistry()
            def fail(args):
                raise ValueError('broken handler')
            registry.register('probe', {}, fail)
            agent = AgentLoop(client, registry, TodoManager(), None, SkillLoader(), MessageBus(Path(tmp)), None)
            self.assertEqual(agent.run('test'), 'recovered')
            results = [m for m in client.requests[-1] if m['role'] == 'tool']
            self.assertEqual(len(results), 3)
            self.assertTrue(all('Error' in m['content'] for m in results))

    def test_main_iteration_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Client([message(calls=[tool_call('1')])])
            registry = ToolRegistry()
            registry.register('probe', {}, lambda args: 'ok')
            agent = AgentLoop(client, registry, TodoManager(), None, SkillLoader(), MessageBus(Path(tmp)), None)
            agent.MAX_ITERATIONS = 1
            self.assertIn('iteration limit', agent.run('test'))
            self.assertEqual(agent.messages[-1]['role'], 'tool')

    def test_message_names_and_concurrent_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bus = MessageBus(root)
            for name in ('../escape', '/absolute', '', 'a/b'):
                with self.assertRaises(ValueError):
                    bus.send('lead', name, 'bad')
            received = []
            finished = threading.Event()
            def consume():
                other = MessageBus(root)
                while not finished.is_set():
                    received.extend(json.loads(other.read_inbox('worker')))
                received.extend(json.loads(other.read_inbox('worker')))
            reader = threading.Thread(target=consume)
            reader.start()
            try:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(lambda i: MessageBus(root).send('lead', 'worker', str(i)), range(100)))
            finally:
                finished.set()
                reader.join(timeout=3)
            self.assertFalse(reader.is_alive())
            self.assertEqual(sorted(int(m['content']) for m in received), list(range(100)))
            self.assertEqual(bus.read_inbox('worker'), '[]')

    def test_idle_waits_resumes_and_reports_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            bus = MessageBus(Path(tmp))
            client = Client([message('first result'), message('second result')])
            manager = TeammateManager(Path(tmp), client, 'model', bus)
            registry = manager._setup_teammate_registry('worker')
            self.assertTrue(registry.has_tool('send'))
            self.assertTrue(registry.has_tool('read_inbox'))
            try:
                manager.spawn('worker', 'tester', 'first task')
                self.wait_for(lambda: manager.list_members()[0]['status'] == 'idle')
                time.sleep(0.25)
                self.assertEqual(len(client.requests), 1)
                self.assertEqual(json.loads(bus.read_inbox('lead'))[0]['content'], 'first result')
                bus.send('lead', 'worker', 'second task')
                self.wait_for(lambda: len(client.requests) == 2 and manager.list_members()[0]['status'] == 'idle')
                self.assertEqual(json.loads(bus.read_inbox('lead'))[0]['content'], 'second result')
            finally:
                manager.shutdown_all()
            self.assertFalse(manager.teammates['worker'].thread.is_alive())
            self.assertEqual(manager.list_members()[0]['status'], 'shutdown')
            self.assertEqual(json.loads(manager.config_path.read_text())['members'][0]['status'], 'shutdown')

    def test_restart_marks_stale_members_shutdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / 'config.json').write_text(json.dumps({'members': [{'name': 'old', 'role': 'test', 'status': 'working'}]}))
            manager = TeammateManager(path, Client([]), 'model', MessageBus(path))
            self.assertEqual(manager.list_members()[0]['status'], 'shutdown')
            self.assertEqual(manager.teammates, {})

    def test_subagent_resets_todo_and_returns_bad_arguments(self):
        client = Client([message(calls=[NS(id='bad', function=NS(name='todo', arguments='{'))]),
                         message('recovered'), message('next task')])
        agent = SubAgent(client, 'model')
        agent.todo_manager.update([{'id': 'old', 'text': 'stale'}])
        self.assertEqual(agent.run('first task'), 'recovered')
        self.assertEqual(agent.todo_manager.items, [])
        self.assertIn('Invalid JSON', client.requests[1][-1]['content'])
        agent.todo_manager.update([{'id': 'old', 'text': 'stale'}])
        self.assertEqual(agent.run('second task'), 'next task')
        self.assertEqual(agent.todo_manager.items, [])

    def test_teammate_limit_reports_and_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            bus = MessageBus(Path(tmp))
            client = Client([message(calls=[NS(id='bad', function=NS(name='todo', arguments='{'))])])
            manager = TeammateManager(Path(tmp), client, 'model', bus)
            manager.MAX_ITERATIONS = 1
            try:
                manager.spawn('worker', 'tester', 'task')
                self.wait_for(lambda: manager.list_members()[0]['status'] == 'shutdown')
                self.assertIn('iteration limit', json.loads(bus.read_inbox('lead'))[0]['content'])
                self.assertEqual(len(client.requests), 1)
            finally:
                manager.shutdown_all()

    def test_teammate_api_error_waits_for_new_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            bus = MessageBus(Path(tmp))
            client = Client([])  # Exhaustion simulates a failed API call.
            manager = TeammateManager(Path(tmp), client, 'model', bus)
            try:
                manager.spawn('worker', 'tester', 'task')
                self.wait_for(lambda: manager.list_members()[0]['status'] == 'idle')
                time.sleep(0.2)
                self.assertEqual(len(client.requests), 1)
                self.assertEqual(json.loads(bus.read_inbox('lead'))[0]['type'], 'error')
                client.replies = iter([message('recovered')])
                bus.send('lead', 'worker', 'try a fresh task')
                self.wait_for(lambda: len(client.requests) == 2 and manager.list_members()[0]['status'] == 'idle')
                self.assertEqual(json.loads(bus.read_inbox('lead'))[0]['content'], 'recovered')
            finally:
                manager.shutdown_all()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail('Timed out waiting for worker')


if __name__ == '__main__':
    unittest.main()
