"""Workspace file edits with a reviewed diff and stale-base detection."""
import os
from pathlib import Path
import stat
import tempfile

from agent_runtime.config import Settings
from agent_runtime.execution import current_task
from agent_runtime.review import review_queue

WRITE_FILE_TOOL = {'type': 'function', 'function': {
    'name': 'write_file', 'description': 'Propose a UTF-8 file edit. The operator must approve its diff before execution.',
    'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'content': {'type': 'string'}},
                   'required': ['path', 'content']}}}


def handle_write_file(args):
    task = current_task.get()
    settings = task.settings if task else Settings.from_env()
    root = Path(settings.workdir).resolve()
    path = (root / args['path']).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('Path escapes workspace or names its root')
    if '.git' in path.relative_to(root).parts or path.is_relative_to(settings.state_path):
        raise ValueError('Git metadata and runtime state cannot be edited with write_file')
    content = args['content']
    if not isinstance(content, str) or len(content.encode('utf-8')) > settings.write_limit_bytes:
        raise ValueError('Content exceeds the file edit limit or is not text')
    existed = path.exists()
    if existed:
        if not path.is_file() or path.stat().st_size > settings.write_limit_bytes:
            raise ValueError('Only bounded regular UTF-8 files can be edited')
        before = path.read_text(encoding='utf-8')
    else:
        before = ''
    operation = {'tool': 'write_file', 'path': str(path), 'workdir': str(root),
                 'before': before, 'existed': existed, 'content': content}
    review_queue(settings).require(operation, task.task_id if task else None)
    if task:
        task.check()
    # Recheck immediately before replacement; never overwrite a stale reviewed base.
    if path.exists() != existed or (existed and path.read_text(encoding='utf-8') != before):
        raise ValueError('File changed since review; request a fresh diff')
    mode = stat.S_IMODE(path.stat().st_mode) if existed else 0o644
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.resolve() != path.parent or not path.parent.is_relative_to(root):
        raise ValueError('Parent path changed since review')
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.easyagentic-edit-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return f'Updated {path.relative_to(root)}'
