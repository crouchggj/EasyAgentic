"""Validated, centralized runtime settings (no credential persistence)."""
from dataclasses import dataclass, fields
import math
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    model: str = 'gpt-4o'
    max_rounds: int = 50
    subagent_rounds: int = 30
    max_tokens: int = 200000
    output_tokens: int = 2048
    context_tokens: int = 16000
    task_timeout: float = 300.0
    api_timeout: float = 30.0
    retries: int = 2
    max_cost_usd: float = 0.0
    input_usd_per_million: float = 0.0
    output_usd_per_million: float = 0.0
    shell_mode: str = 'disabled'
    shell_timeout: float = 30.0
    shell_output_bytes: int = 64000
    docker_image: str = ''
    team_wait_timeout: float = 30.0
    max_teammates: int = 8
    workdir: str = '.'
    state_dir: str = '.easyagentic'

    def __post_init__(self):
        if not self.model.strip():
            raise ValueError("model must be nonempty")
        for name in ('max_rounds', 'subagent_rounds', 'max_tokens', 'output_tokens',
                     'context_tokens', 'shell_output_bytes', 'max_teammates'):
            if getattr(self, name) <= 0:
                raise ValueError(f'{name} must be positive')
        for name in ('task_timeout', 'api_timeout', 'shell_timeout', 'team_wait_timeout'):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f'{name} must be finite and positive')
        for name in ('max_cost_usd', 'input_usd_per_million', 'output_usd_per_million'):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) < 0:
                raise ValueError(f'{name} must be finite and nonnegative')
        if self.retries < 0:
            raise ValueError('retries must be nonnegative')
        if self.context_tokens <= self.output_tokens + 256:
            raise ValueError('context_tokens must leave room for input and output')
        if self.max_cost_usd and not (self.input_usd_per_million and self.output_usd_per_million):
            raise ValueError('Cost limits require explicit input and output USD-per-million rates')
        if self.shell_mode not in ('disabled', 'local', 'docker'):
            raise ValueError('shell_mode must be disabled, local or docker')

    @classmethod
    def from_env(cls):
        defaults = cls()
        values = {}
        for field in fields(cls):
            key = 'EASYAGENTIC_' + field.name.upper()
            if key in os.environ:
                values[field.name] = type(getattr(defaults, field.name))(os.environ[key])
        values.setdefault('model', os.environ.get('MODEL', defaults.model))
        values.setdefault('workdir', os.environ.get('WORKDIR', defaults.workdir))
        return cls(**values)

    @property
    def state_path(self):
        return Path(self.state_dir).resolve()
