"""Optional real Docker isolation checks; provide a pre-pulled pinned image."""
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from agent_runtime.config import Settings
from agent_runtime.execution import Task, task_scope
from tools.bash import docker_command
from support import reviewed_shell as handle_bash


@unittest.skipUnless(os.environ.get('EASYAGENTIC_TEST_DOCKER_IMAGE'), 'Docker integration requires an explicit pre-pulled digest')
class DockerTests(unittest.TestCase):
    def test_isolation_output_cancellation_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'input').write_text('workspace-ok')
            settings = Settings(workdir=tmp, state_dir=tmp, shell_mode='docker', shell_timeout=10,
                                docker_image=os.environ['EASYAGENTIC_TEST_DOCKER_IMAGE'], shell_output_bytes=1024)
            with task_scope(Task(settings)):
                result = handle_bash({'command': 'set -e; cat input; test ! -e /var/run/docker.sock; test ! -e /root/.aws; test -z "$OPENAI_API_KEY"; test ! -d /sys/class/net/eth0; if touch /root/test 2>/dev/null; then exit 1; fi; echo isolated'})
                self.assertIn('workspace-ok', result)
                self.assertIn('isolated', result)
                self.assertNotIn('Error:', result)
                self.assertIn('output limit', handle_bash({'command': 'yes output'}))
            task = Task(settings)
            timer = threading.Timer(0.3, task.stop.set)
            timer.start()
            try:
                with task_scope(task):
                    start = time.monotonic()
                    self.assertIn('cancelled', handle_bash({'command': 'sleep 20'}))
                    self.assertLess(time.monotonic()-start, 5)
            finally:
                timer.cancel()
            remaining = subprocess.run(docker_command() + ['ps', '-aq', '--filter', 'name=easyagentic-'],
                                       capture_output=True, text=True, check=True, timeout=10)
            self.assertEqual(remaining.stdout.strip(), '')
