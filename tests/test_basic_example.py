"""Exercise the teaching loop without an API credential or network request."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from openai.types.chat import ChatCompletionMessage

from agent_runtime.config import Settings
from agent_runtime.execution import Task, task_scope
from examples.basic_agent import run_agent


def assistant(content=None, calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=ChatCompletionMessage(
        role='assistant', content=content, tool_calls=calls))])


def call(identifier, arguments, name='read_file'):
    return {'id': identifier, 'type': 'function',
            'function': {'name': name, 'arguments': arguments}}


class BasicExampleTests(unittest.TestCase):
    def test_read_round_trip_preserves_tool_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'lesson.txt').write_text('lesson-ready', encoding='utf-8')
            client = Mock()
            def respond(**kwargs):
                messages = kwargs['messages']
                if len(messages) == 2:
                    return assistant(calls=[call('read-1', '{"path":"lesson.txt"}')])
                self.assertEqual(messages[-2]['tool_calls'][0]['id'], 'read-1')
                self.assertEqual(messages[-1], {'role': 'tool', 'tool_call_id': 'read-1',
                                               'content': 'lesson-ready'})
                return assistant('lesson complete')
            client.chat.completions.create.side_effect = respond
            with task_scope(Task(Settings(workdir=directory, state_dir=directory))):
                self.assertEqual(run_agent(client, 'read lesson'), 'lesson complete')
            self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_invalid_calls_receive_results_and_loop_is_bounded(self):
        client = Mock()
        client.chat.completions.create.return_value = assistant(calls=[
            call('bad-json', '{'), call('bad-shape', '[]'),
            call('unknown', '{}', 'bash'), call('escape', '{"path":"../outside"}'),
        ])
        with tempfile.TemporaryDirectory() as directory:
            with task_scope(Task(Settings(workdir=directory, state_dir=directory))):
                self.assertEqual(run_agent(client, 'try tools', max_rounds=1), '(round limit reached)')
        messages = client.chat.completions.create.call_args.kwargs['messages']
        results = messages[-4:]
        self.assertEqual([item['tool_call_id'] for item in results],
                         ['bad-json', 'bad-shape', 'unknown', 'escape'])
        self.assertTrue(all('error' in item['content'].lower() for item in results))
        self.assertIn('escapes workspace', results[-1]['content'])
        client.chat.completions.create.assert_called_once()
