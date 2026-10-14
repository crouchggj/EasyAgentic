"""Application boundaries and backwards-compatible import contracts."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent_runtime.config import Settings
from easyagentic.bootstrap import create_application
from easyagentic.cli import run_cli
from easyagentic.commands import CommandRouter


class ApplicationTests(unittest.TestCase):
    def test_import_orders_preserve_public_identities_without_startup(self):
        for first in ('easyagentic.agents.teammates', 'tools', 'main'):
            code = f'''
import importlib
importlib.import_module({first!r})
from tools import *
from main import AgentLoop
from easyagentic.agents.lead import AgentLoop as Lead
from easyagentic.agents.teammates import TeammateManager as Manager
from easyagentic.agents.subagent import SubAgent as Child
from tools.team import TeammateManager as LegacyManager
from tools.subagent import SubAgent as LegacyChild
assert AgentLoop is Lead
assert TeammateManager is LegacyManager is Manager
assert SubAgent is LegacyChild is Child
'''
            with tempfile.TemporaryDirectory() as directory:
                result = subprocess.run([sys.executable, '-c', code], text=True,
                                        capture_output=True, timeout=10,
                                        env={**os.environ, 'EASYAGENTIC_STATE_DIR': directory})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_client_ownership_and_initialization_failure_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(state_dir=directory)
            borrowed = Mock()
            app = create_application(settings, client=borrowed, skills_dir=directory)
            with patch.object(app.team_manager, 'shutdown_all') as shutdown:
                app.close()
                app.close()
                shutdown.assert_called_once()
            borrowed.close.assert_not_called()
            owned = Mock()
            with patch('easyagentic.bootstrap.CancellableClient', return_value=owned):
                with create_application(settings, api_key='fixture-only', skills_dir=directory):
                    pass
            owned.close.assert_called_once()
            broken = Mock()
            with patch('easyagentic.bootstrap.CancellableClient', return_value=broken), \
                    patch('easyagentic.bootstrap.SessionStore', side_effect=RuntimeError('fixture')), \
                    patch('easyagentic.bootstrap.TeammateManager.shutdown_all') as shutdown:
                with self.assertRaisesRegex(RuntimeError, 'fixture'):
                    create_application(settings, api_key='fixture-only', skills_dir=directory)
                shutdown.assert_called_once()
            broken.close.assert_called_once()

    def test_operator_commands_route_without_model_execution(self):
        app = SimpleNamespace(agent=Mock(messages=[]), team_manager=Mock(),
                              reviews=Mock(), journal=Mock())
        app.reviews.decide.return_value = {'status': 'approved', 'id': 'review-id'}
        router = CommandRouter(app)
        result = router.handle('/approve review-id')
        self.assertTrue(result.handled)
        app.reviews.decide.assert_called_once_with('review-id', True)
        app.agent.checkpoint.assert_called_once_with('review_decided')
        app.team_manager.notify_review.assert_called_once()
        app.agent.run.assert_not_called()
        result = router.handle('/continue')
        self.assertFalse(result.handled)
        self.assertIn('do not repeat completed actions', result.query)
        router.handle('/reset')
        app.agent.bus.discard.assert_called_once_with('lead')
        app.agent.reset.assert_called_once()

    def test_cli_presents_errors_and_continues_without_closing_caller_resources(self):
        app = SimpleNamespace(agent=Mock(messages=[]), team_manager=Mock(),
                              reviews=Mock(), journal=Mock(), skills=Mock())
        app.skills.get_skill_names.return_value = []
        app.reviews.preview.side_effect = ValueError('unknown review')
        app.agent.run.side_effect = RuntimeError('private request body')
        inputs = iter(['/review missing', 'hello', 'quit'])
        output = []
        run_cli(app, input_fn=lambda _: next(inputs), output_fn=output.append)
        self.assertIn('Command refused: unknown review', output)
        self.assertTrue(any('RuntimeError' in line for line in output))
        self.assertFalse(any('private request body' in line for line in output))
        self.assertEqual(output[-1], 'Goodbye!')
        app.team_manager.shutdown_all.assert_not_called()
