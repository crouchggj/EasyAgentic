"""Bounded file reading under the configured workspace root."""
from pathlib import Path

from agent_runtime.config import Settings
from agent_runtime.execution import current_task

READ_FILE_TOOL = {
    'type': 'function', 'function': {
        'name': 'read_file', 'description': 'Read a UTF-8 file within the configured workspace.',
        'parameters': {'type': 'object', 'properties': {
            'path': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1}},
            'required': ['path']},
    },
}


def safe_path(value):
    task = current_task.get()
    settings = task.settings if task else Settings.from_env()
    root = Path(settings.workdir).resolve()
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f'Path escapes workspace: {value}')
    return path


def handle_read_file(args):
    task = current_task.get()
    settings = task.settings if task else Settings.from_env()
    limit = args.get('limit')
    try:
        if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
            raise ValueError('limit must be a positive integer')
        path = safe_path(args['path'])
        with path.open('rb') as stream:
            data = stream.read(settings.shell_output_bytes + 1)
        truncated = len(data) > settings.shell_output_bytes
        text = data[:settings.shell_output_bytes].decode('utf-8', errors='replace')
        if limit:
            text = '\n'.join(text.splitlines()[:limit])
        return text.rstrip('\n') + ('\n[truncated for output limit]' if truncated else '')
    except FileNotFoundError:
        return f"Error: File not found: {args['path']}"
    except Exception as exc:
        return f'Error reading file: {exc}'
