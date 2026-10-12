"""Explicitly opt-in live authentication/model check; never runs by default."""
import os
import tempfile
import unittest
from agent_runtime.client import CancellableClient
from agent_runtime.config import Settings
from agent_runtime.execution import Task, completion, task_scope
from tools import ToolRegistry


@unittest.skipUnless(os.environ.get('EASYAGENTIC_RUN_LIVE') == '1', 'Live API test requires explicit opt-in')
class LiveModelTest(unittest.TestCase):
    def test_model_response(self):
        key = os.environ.get('OPENAI_API_KEY') or os.environ.get('EASYAGENTIC_API_KEY')
        self.assertTrue(key, 'Opted-in live test requires a configured API credential')
        settings = Settings.from_env()
        client = CancellableClient(api_key=key, base_url=os.environ.get('OPENAI_URL', 'https://api.openai.com/v1'), max_retries=0)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                from dataclasses import replace
                task = Task(replace(settings, state_dir=tmp))
                with task_scope(task):
                    response = completion(client, settings.model,
                        [{'role': 'user', 'content': 'Reply with exactly EASYAGENTIC_READY.'}], ToolRegistry(), task)
                self.assertIn('EASYAGENTIC_READY', response.choices[0].message.content or '')
        finally:
            client.close()
