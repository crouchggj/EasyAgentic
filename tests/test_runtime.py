import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from agent_runtime.config import Settings
from agent_runtime.execution import Budget, Task, TaskStopped, compact, completion, task_scope, token_bound
from agent_runtime.storage import SessionStore, repair_history
from main import AgentLoop
from openai import APIConnectionError, APIStatusError
import httpx2
from tools import MessageBus, SkillLoader, SubAgent, TeammateManager, TodoManager, ToolRegistry, setup_registry
from tools.bash import handle_bash
from tools.read_file import handle_read_file
from test_agents import Client, message, tool_call


class RuntimeTests(unittest.TestCase):
    def test_settings_validation_and_env(self):
        with patch.dict(os.environ, {'EASYAGENTIC_MAX_ROUNDS': '3', 'MODEL': 'custom'}, clear=True):
            self.assertEqual(Settings.from_env().max_rounds, 3)
            self.assertEqual(Settings.from_env().model, 'custom')
        for kwargs in ({'max_tokens': 0}, {'api_timeout': float('nan')}, {'shell_mode': 'bad'},
                       {'max_cost_usd': 1}, {'context_tokens': 100}, {'retries': -1}):
            with self.assertRaises(ValueError):
                Settings(**kwargs)

    def test_shared_token_and_cost_reservations(self):
        settings = Settings(max_tokens=100, max_cost_usd=0.01,
                            input_usd_per_million=100, output_usd_per_million=100)
        task = Task(settings)
        child = task.child()
        self.assertIs(task.budget, child.budget)
        reservation = child.budget.reserve(40, 20)
        with self.assertRaises(TaskStopped):
            task.budget.reserve(41, 0)
        task.budget.settle(reservation, NS(prompt_tokens=10, completion_tokens=5))
        self.assertEqual(task.budget.tokens, 15)
        self.assertAlmostEqual(task.budget.cost, 0.0015)
        settings = Settings(max_tokens=100000, max_cost_usd=0.001,
                            input_usd_per_million=100, output_usd_per_million=100)
        with self.assertRaisesRegex(TaskStopped, 'cost'):
            Budget(settings).reserve(100, 100)

    def test_budget_reservations_are_atomic(self):
        budget = Budget(Settings(max_tokens=100))
        success = []
        def reserve():
            try:
                budget.reserve(10, 0)
                success.append(True)
            except TaskStopped:
                pass
        threads = [threading.Thread(target=reserve) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(success), 10)
        self.assertEqual(budget.tokens, 100)

    def test_deadline_and_parent_cancellation(self):
        task = Task(Settings(), deadline=time.monotonic() - 1)
        with self.assertRaisesRegex(TaskStopped, 'deadline'):
            task.check()
        task = Task(Settings())
        child = task.child()
        task.stop.set()
        with self.assertRaisesRegex(TaskStopped, 'cancelled'):
            child.check()

    def test_compaction_preserves_tool_protocol_and_current_request(self):
        messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'current objective'}]
        for i in range(10):
            messages += [{'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': str(i), 'type': 'function', 'function': {'name': 'probe', 'arguments': '{}'}}]},
                {'role': 'tool', 'tool_call_id': str(i), 'content': 'x' * 500}]
        self.assertTrue(compact(messages, 2000))
        self.assertLessEqual(token_bound(messages), 2000)
        self.assertIn('current objective', [m.get('content') for m in messages])
        calls = {c['id'] for m in messages for c in m.get('tool_calls') or []}
        results = {m['tool_call_id'] for m in messages if m['role'] == 'tool'}
        self.assertEqual(calls, results)
        self.assertIn('9', calls)
        with self.assertRaises(TaskStopped):
            compact([{'role': 'user', 'content': 'x' * 10000}], 1000)

    def test_restore_repairs_interrupted_calls_without_replaying(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / 'session.json')
            store.save([{'role': 'user', 'content': 'work'},
                        {'role': 'assistant', 'content': None, 'tool_calls': [
                            {'id': '1', 'type': 'function', 'function': {'name': 'probe', 'arguments': '{}'}},
                            {'id': '2', 'type': 'function', 'function': {'name': 'probe', 'arguments': '{}'}}]},
                        {'role': 'tool', 'tool_call_id': '1', 'content': 'completed'}],
                       [{'id': '1', 'text': 'task', 'status': 'pending'}], 'running')
            client = Client([message('resumed')])
            registry = ToolRegistry()
            executions = []
            registry.register('probe', {}, lambda args: executions.append(args) or 'ok')
            agent = AgentLoop(client, registry, TodoManager(), None, SkillLoader(), MessageBus(Path(tmp)), None,
                              settings=Settings(state_dir=tmp), store=store)
            agent.restore()
            self.assertEqual(agent.messages[-1]['tool_call_id'], '2')
            self.assertIn('side effects', agent.messages[-1]['content'])
            self.assertEqual(agent.run('continue'), 'resumed')
            self.assertEqual(executions, [])
            self.assertEqual(store.load()['status'], 'completed')
            agent.reset()
            self.assertEqual(store.load()['messages'], [])
            self.assertEqual(agent.todo_manager.items, [])

    def test_transient_retries_only_and_safe_telemetry(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Task(Settings(state_dir=tmp, retries=2))
            registry = ToolRegistry()
            request = httpx2.Request('POST', 'https://example.com')
            error = APIConnectionError(request=request)
            response = NS(choices=[NS(message=message('ok'))], usage=NS(prompt_tokens=5, completion_tokens=2))
            client = NS(chat=NS(completions=NS(create=None)))
            with patch.object(task, 'wait'), patch.object(client.chat.completions, 'create', side_effect=[error, response]) as create:
                self.assertIs(completion(client, 'test', [{'role': 'user', 'content': 'secret-prompt'}], registry, task), response)
                self.assertEqual(create.call_count, 2)
            self.assertNotIn('secret-prompt', (Path(tmp) / 'events.jsonl').read_text())
            bad = APIStatusError('secret-body', response=httpx2.Response(400, request=request), body={})
            with patch.object(client.chat.completions, 'create', side_effect=bad) as create:
                with self.assertRaises(APIStatusError):
                    completion(client, 'test', [{'role': 'user', 'content': 'x'}], registry, task)
                self.assertEqual(create.call_count, 1)
            self.assertNotIn('secret-body', (Path(tmp) / 'events.jsonl').read_text())

    def test_team_controls_and_explicit_checkpoint_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(state_dir=tmp)
            bus = MessageBus(Path(tmp))
            client = Client([message('result')])
            manager = TeammateManager(Path(tmp), client, 'model', bus, settings=settings)
            registry = setup_registry(TodoManager(), team_manager=manager)
            for name in ('team_status', 'team_wait', 'team_cancel', 'team_resume'):
                self.assertTrue(registry.has_tool(name))
            try:
                result = json.loads(manager.spawn('worker', 'tester', 'task'))
                self.assertTrue(result['task_id'])
                state = json.loads(manager.wait('worker', 2))
                self.assertEqual(state['result'], 'result')
                self.assertEqual(state['outcome'], 'completed')
                manager.shutdown('worker')
                client.replies = iter([message('resumed result')])
                self.assertIn('task_id', json.loads(manager.resume('worker')))
                state = json.loads(manager.wait('worker', 2))
                self.assertEqual(state['result'], 'resumed result')
                self.assertTrue(any('checkpoint' in str(m.get('content')) for m in client.requests[-1]))
            finally:
                manager.shutdown_all()

    def test_main_waits_for_teammate_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            class Manager:
                waiting = True
                def has_pending(self):
                    return self.waiting
                def wait_all(self, timeout):
                    bus.send('worker', 'lead', 'finished')
                    self.waiting = False
            bus = MessageBus(Path(tmp))
            client = Client([message('provisional'), message('includes finished teammate')])
            agent = AgentLoop(client, ToolRegistry(), TodoManager(), None, SkillLoader(), bus, Manager(), settings=Settings(state_dir=tmp))
            self.assertEqual(agent.run('work'), 'includes finished teammate')
            self.assertTrue(any('finished' in str(m.get('content')) for m in client.requests[-1]))

    def test_main_collects_results_that_finish_during_final_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            bus = MessageBus(Path(tmp))
            class Manager:
                def has_pending(self):
                    return False
            client = Client([message('provisional'), message('collected')])
            original_create = client.create
            def create(**kwargs):
                if not client.requests:
                    bus.send('worker', 'lead', 'finished during request', 'result')
                return original_create(**kwargs)
            client.chat.completions.create = create
            agent = AgentLoop(client, ToolRegistry(), TodoManager(), None, SkillLoader(), bus, Manager(), settings=Settings(state_dir=tmp))
            self.assertEqual(agent.run('work'), 'collected')
            self.assertEqual(len(client.requests), 2)

    def test_synchronous_child_cannot_bypass_parent_token_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(max_tokens=100, state_dir=tmp)
            task = Task(settings)
            client = Client([message('must not be reached')])
            child = SubAgent(client, 'model', settings=settings)
            with task_scope(task):
                self.assertIn('token budget', child.run('expensive request'))
            self.assertEqual(client.requests, [])

    def test_read_file_uses_task_workspace_and_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'large').write_text('a' * 1000)
            with task_scope(Task(Settings(workdir=tmp, shell_output_bytes=100))):
                output = handle_read_file({'path': 'large'})
                self.assertTrue(output.startswith('a' * 100))
                self.assertIn('truncated', output)
                self.assertIn('escapes workspace', handle_read_file({'path': '../outside'}))
                self.assertIn('positive integer', handle_read_file({'path': 'large', 'limit': -1}))

    def test_cancelled_tool_batch_still_has_every_result(self):
        from agent_runtime.execution import tool_results
        with tempfile.TemporaryDirectory() as tmp:
            task = Task(Settings(state_dir=tmp))
            registry = ToolRegistry()
            invoked = []
            def stop(args):
                task.stop.set()
                invoked.append(True)
                return 'first action completed'
            registry.register('probe', {}, stop)
            results = tool_results(message(calls=[tool_call('first'), tool_call('second')]), registry, task)
            self.assertEqual([r['tool_call_id'] for r in results], ['first', 'second'])
            self.assertEqual(len(invoked), 1)
            self.assertIn('cancelled', results[1]['content'])

    def test_shell_disabled_and_digest_required(self):
        with task_scope(Task(Settings())):
            self.assertIn('disabled', handle_bash({'command': 'echo should-not-run'}))
        with task_scope(Task(Settings(shell_mode='docker', docker_image='alpine:latest'))):
            self.assertIn('pinned', handle_bash({'command': 'echo should-not-run'}))

    @unittest.skipIf(os.name == 'nt', 'Local unsafe mode process-group checks require POSIX')
    def test_local_shell_cwd_clean_environment_timeout_and_descendants(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(workdir=tmp, state_dir=tmp, shell_mode='local', shell_timeout=0.2)
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'secret'}), task_scope(Task(settings)):
                self.assertEqual(handle_bash({'command': 'pwd'}), str(Path(tmp).resolve()))
                self.assertEqual(handle_bash({'command': 'printf "%s" "${OPENAI_API_KEY-unset}"'}), 'unset')
                start = time.monotonic()
                self.assertIn('timed out', handle_bash({'command': 'sleep 20 & wait'}))
                self.assertLess(time.monotonic() - start, 3)
                self.assertIn('code 2', handle_bash({'command': 'exit 2'}))

    @unittest.skipIf(os.name == 'nt', 'Local mode disabled on Windows')
    def test_shell_cancellation_and_output_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(workdir=tmp, state_dir=tmp, shell_mode='local', shell_timeout=10, shell_output_bytes=512)
            task = Task(settings)
            timer = threading.Timer(0.1, task.stop.set)
            timer.start()
            try:
                with task_scope(task):
                    start = time.monotonic()
                    self.assertIn('cancelled', handle_bash({'command': 'sleep 20'}))
                    self.assertLess(time.monotonic()-start, 2)
            finally:
                timer.cancel()
            with task_scope(Task(settings)):
                output = handle_bash({'command': 'yes very-large-output'})
                self.assertIn('output limit', output)
                self.assertLess(len(output), 600)
