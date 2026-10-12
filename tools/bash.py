"""Opt-in shell execution with bounded output, deadlines and process-tree cleanup."""
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
import uuid

from agent_runtime.config import Settings
from agent_runtime.execution import current_task, TaskStopped

BASH_TOOL = {
    'type': 'function', 'function': {
        'name': 'bash', 'description': 'Execute a command using the configured shell policy (disabled by default).',
        'parameters': {'type': 'object', 'properties': {'command': {'type': 'string'}}, 'required': ['command']},
    },
}


def docker_command():
    return ['docker', '--host=unix:///var/run/docker.sock'] if os.name != 'nt' else ['docker']


def terminate_tree(process):
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)


def handle_bash(args):
    task = current_task.get()
    settings = task.settings if task else Settings.from_env()
    command = args['command']
    if not isinstance(command, str) or not command.strip():
        return 'Error: command must be a nonempty string'
    if os.name == 'nt' and settings.shell_mode == 'local':
        return 'Error: local mode is unsupported on Windows; use Docker for reliable process-tree cleanup'
    if settings.shell_mode == 'disabled':
        return 'Error: shell execution disabled. The operator must enable docker or explicitly opt into unsafe local mode.'
    root = Path(settings.workdir).resolve(strict=True)
    if not root.is_dir():
        return 'Error: workdir must be a directory'
    if task:
        task.check()
    name = 'easyagentic-' + uuid.uuid4().hex
    if settings.shell_mode == 'docker':
        if not re.fullmatch(r'[A-Za-z0-9._/:\-]+@sha256:[a-f0-9]{64}', settings.docker_image):
            return 'Error: docker_image must be pinned using repository@sha256:<64 hex digits>; pre-pull it before running.'
        if ',' in str(root):
            return 'Error: Docker workdir cannot contain a comma'
        argv = docker_command() + ['run', '--pull=never', '--rm', '--name', name,
                '--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                '--log-driver=none', '--pids-limit=64', '--memory=256m', '--cpus=1', '--tmpfs=/tmp:rw,nosuid,nodev,size=64m',
                '--mount', f'type=bind,src={root},dst=/workspace', '--workdir=/workspace']
        if hasattr(os, 'getuid'):
            argv += ['--user', f'{os.getuid()}:{os.getgid()}']
        for variable in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'):
            argv += ['--env', variable + '=']
        argv += ['--entrypoint=/bin/sh', settings.docker_image, '-c', command]
        # Docker needs its configured registry selectors, but never application credentials.
        env = {k: v for k, v in os.environ.items() if k in
               ('PATH', 'SYSTEMROOT', 'WINDIR', 'DOCKER_CONFIG', 'HOME', 'USERPROFILE')}
    else:
        argv = ['cmd.exe', '/d', '/s', '/c', command] if os.name == 'nt' else ['/bin/sh', '-c', command]
        # Local mode is explicitly unsafe; cwd and clean env are not a sandbox.
        env = {k: v for k, v in os.environ.items() if k in ('PATH', 'SYSTEMROOT', 'WINDIR')}
    deadline = time.monotonic() + settings.shell_timeout
    if task:
        deadline = min(deadline, task.deadline)
    process = None
    failure = None
    captured = bytearray()
    overflow = threading.Event()
    reader = None
    def drain():
        while True:
            chunk = process.stdout.read1(4096)
            if not chunk:
                return
            remaining = settings.shell_output_bytes - len(captured)
            captured.extend(chunk[:remaining])
            if len(chunk) > remaining:
                overflow.set()
                return
    try:
        process = subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=os.name != 'nt',
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        while process.poll() is None:
            if task:
                task.check()
            if time.monotonic() >= deadline:
                failure = 'Error: command timed out'
                break
            if overflow.is_set():
                failure = 'Error: command output limit exceeded'
                break
            time.sleep(0.02)
    except (TaskStopped, KeyboardInterrupt):
        failure = 'Error: command cancelled'
        if task:
            task.stop.set()
    except OSError as exc:
        failure = f'Error: command could not start ({type(exc).__name__})'
    finally:
        if process is not None:
            terminate_tree(process)
        if settings.shell_mode == 'docker':
            try:
                subprocess.run(docker_command() + ['rm', '-f', name], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
            except (OSError, subprocess.TimeoutExpired):
                failure = 'Error: container cleanup failed; operator must inspect ' + name
        if reader:
            reader.join(timeout=5)
            if not reader.is_alive():
                process.stdout.close()
            else:
                failure = 'Error: output reader did not terminate'
    if overflow.is_set() and not failure:
        failure = 'Error: command output limit exceeded'
    text = captured.decode('utf-8', errors='replace').strip()
    if failure:
        return failure + ('\n' + text if text else '')
    if process.returncode:
        return f'Error: command exited with code {process.returncode}\n{text}'
    return text or 'Command executed successfully (no output)'
