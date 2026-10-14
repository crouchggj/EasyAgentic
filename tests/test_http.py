"""Exercise real SDK serialization, retries, cancellation and CLI against local HTTP."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest

from agent_runtime.client import CancellableClient
from agent_runtime.config import Settings
from agent_runtime.execution import Task, TaskStopped, completion, task_scope
from main import AgentLoop
from tools import MessageBus, SkillLoader, TodoManager, ToolRegistry


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.started = threading.Event()
        self.delay = False
        self.retry = False
        self.single = False
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append(request)
                owner.started.set()
                if owner.retry and len(owner.requests) == 1:
                    self.send_response(429)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Retry-After', '0')
                    self.end_headers()
                    self.wfile.write(b'{"error":{"message":"rate limited","type":"rate_limit_error"}}')
                    return
                if owner.delay:
                    time.sleep(0.5)
                if not owner.single and len(owner.requests) == 1:
                    message = {'role': 'assistant', 'content': None, 'tool_calls': [
                        {'id': 'call_http', 'type': 'function', 'function': {'name': 'probe', 'arguments': '{}'}}]}
                    finish = 'tool_calls'
                else:
                    message = {'role': 'assistant', 'content': 'http-ok'}
                    finish = 'stop'
                payload = json.dumps({'id': 'chatcmpl-local', 'object': 'chat.completion',
                    'created': 0, 'model': request['model'], 'choices': [
                        {'index': 0, 'message': message, 'finish_reason': finish}],
                    'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}).encode()
                try:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'
        self.client = CancellableClient(api_key='local-fixture-only', base_url=self.url, max_retries=0)
        self.temporary = tempfile.TemporaryDirectory()
        self.settings = Settings(state_dir=self.temporary.name, api_timeout=2)

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def test_sdk_tool_round_trip_and_usage(self):
        registry = ToolRegistry()
        registry.register('probe', {'type': 'function', 'function': {'name': 'probe',
            'parameters': {'type': 'object', 'properties': {}}}}, lambda args: 'tool-http-ok')
        agent = AgentLoop(self.client, registry, TodoManager(), None, SkillLoader(),
                          MessageBus(Path(self.temporary.name)), None, settings=self.settings)
        self.assertEqual(agent.run('use probe'), 'http-ok')
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[1]['messages'][-1]['content'], 'tool-http-ok')
        events = [json.loads(line) for line in (Path(self.temporary.name) / 'events.jsonl').read_text().splitlines()]
        self.assertEqual([e['completion_tokens'] for e in events if e['event'] == 'model_response'], [5, 5])

    def test_inflight_request_is_cancelled(self):
        self.delay = True
        self.single = True
        task = Task(self.settings)
        timer = threading.Timer(0.1, task.stop.set)
        timer.start()
        start = time.monotonic()
        try:
            with task_scope(task), self.assertRaises(TaskStopped):
                completion(self.client, 'model', [{'role': 'user', 'content': 'wait'}], ToolRegistry(), task)
            self.assertLess(time.monotonic()-start, 0.4)
            # A cancelled request must not close the shared client for other agents.
            self.delay = False
            with task_scope(Task(self.settings)):
                response = completion(self.client, 'model', [{'role': 'user', 'content': 'next'}], ToolRegistry(), Task(self.settings))
            self.assertEqual(response.choices[0].message.content, 'http-ok')
        finally:
            timer.cancel()

    def test_sdk_429_retries_once(self):
        self.retry = True
        self.single = True
        task = Task(self.settings)
        with task_scope(task):
            response = completion(self.client, 'model', [{'role': 'user', 'content': 'retry'}], ToolRegistry(), task)
        self.assertEqual(response.choices[0].message.content, 'http-ok')
        self.assertEqual(len(self.requests), 2)

    def test_cli_exit_reset_resume_and_eof(self):
        env = dict(os.environ, OPENAI_API_KEY='local-fixture-only', OPENAI_URL=self.url,
                   EASYAGENTIC_STATE_DIR=self.temporary.name, TEAM_DIR=str(Path(self.temporary.name) / 'team'),
                   EASYAGENTIC_SHELL_MODE='disabled')
        result = subprocess.run([sys.executable, 'main.py'], input='/reset\n/resume\nquit\n',
                                env=env, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Session reset', result.stdout)
        self.assertIn('Session restored', result.stdout)
        self.assertIn('Goodbye!', result.stdout)
        result = subprocess.run([sys.executable, '-m', 'easyagentic'], input='', env=env, text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Goodbye!', result.stdout)
