"""Compatibility entry point; use easyagentic.cli and easyagentic.agents.lead internally."""
from easyagentic.cli import main
from easyagentic.agents.lead import AgentLoop, build_system_prompt

__all__ = ['main', 'AgentLoop', 'build_system_prompt']

if __name__ == '__main__':
    main()
