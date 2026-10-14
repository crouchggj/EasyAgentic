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

    def test_repository_team_workflow(self):
        from dataclasses import replace
        from pathlib import Path
        import json
        from e2e_support import WORKFLOW_QUERY, build_agent
        key = os.environ.get('OPENAI_API_KEY') or os.environ.get('EASYAGENTIC_API_KEY')
        self.assertTrue(key, 'Opted-in workflow test requires an API credential')
        client = CancellableClient(api_key=key, base_url=os.environ.get('OPENAI_URL', 'https://api.openai.com/v1'), max_retries=0)
        manager = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = Path(__file__).resolve().parents[1] / 'README.md'
                (root/'README.md').write_text(source.read_text(encoding='utf-8')[:4000],encoding='utf-8')
                settings = replace(Settings.from_env(),workdir=tmp,state_dir=str(root/'state'),shell_mode='disabled')
                agent,manager = build_agent(client,settings)
                result = agent.run(WORKFLOW_QUERY)
                self.assertIn('E2E_READY',result)
                members = manager.list_members()
                self.assertEqual(len(members),1)
                self.assertEqual(members[0]['name'],'reviewer')
                self.assertEqual(members[0]['outcome'],'completed')
                self.assertTrue(members[0]['result'])
                self.assertEqual(agent.store.load()['status'],'completed')
                self.assertTrue(agent.received_ids)
                events = [json.loads(line) for line in (root/'state'/'events.jsonl').read_text().splitlines()]
                tools = [e['tool'] for e in events if e['event']=='tool_result']
                self.assertIn('spawn',tools)
                self.assertIn('team_wait',tools)
                self.assertGreaterEqual(tools.count('read_file'),2)
                manager.shutdown_all()
        finally:
            if manager:
                manager.shutdown_all()
            client.close()
