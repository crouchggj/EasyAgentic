"""Skill loader for two-layer skill loading."""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class Skill:
    """Represents a loaded skill."""
    name: str
    path: Path
    description: str
    trigger: str
    content: str
    frontmatter: dict


class SkillLoader:
    """Load and manage skills with two-layer loading.

    Layer 1: Skill names in system prompt (low cost)
    Layer 2: Full content via tool_result on demand
    """

    def __init__(self, skills_dir: str = None):
        self.skills_dir = Path(skills_dir or os.environ.get("SKILLS_DIR", "skills"))
        if not self.skills_dir.is_absolute():
            # Make relative to project root
            self.skills_dir = Path(__file__).parent.parent / self.skills_dir
        self._skills: Dict[str, Skill] = {}
        self._load_skills()

    def _parse_frontmatter(self, content: str) -> tuple[dict, str]:
        """Parse YAML frontmatter from skill content."""
        frontmatter = {}
        body = content

        # Match YAML frontmatter between --- markers
        pattern = r'^---\s*\n(.*?)\n---\s*\n(.*)$'
        match = re.match(pattern, content, re.DOTALL)

        if match:
            yaml_content = match.group(1)
            body = match.group(2)

            # Simple YAML parsing for key: value pairs
            for line in yaml_content.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    frontmatter[key.strip()] = value.strip()

        return frontmatter, body

    def _load_skills(self):
        """Recursively scan and load all SKILL.md files."""
        if not self.skills_dir.exists():
            return

        for skill_file in self.skills_dir.rglob("SKILL.md"):
            # Use parent directory name as skill identifier
            skill_name = skill_file.parent.name

            try:
                content = skill_file.read_text(encoding='utf-8')
                frontmatter, body = self._parse_frontmatter(content)

                skill = Skill(
                    name=skill_name,
                    path=skill_file,
                    description=frontmatter.get('description', ''),
                    trigger=frontmatter.get('trigger', ''),
                    content=body.strip(),
                    frontmatter=frontmatter
                )
                self._skills[skill_name] = skill
            except Exception as e:
                print(f"Warning: Failed to load skill {skill_name}: {e}")

    def get_skill_names(self) -> List[str]:
        """Get list of all available skill names."""
        return list(self._skills.keys())

    def get_skill(self, name: str) -> Optional[Skill]:
        """Get a skill by name."""
        return self._skills.get(name)

    def get_skill_content(self, name: str) -> str:
        """Get full content of a skill."""
        skill = self._skills.get(name)
        if skill:
            return skill.content
        return f"Error: Skill '{name}' not found"

    def get_skills_summary(self) -> str:
        """Get summary of all skills for system prompt (Layer 1)."""
        if not self._skills:
            return ""

        lines = ["Available skills:"]
        for name, skill in self._skills.items():
            desc = skill.description[:60] + "..." if len(skill.description) > 60 else skill.description
            trigger = f" (trigger: {skill.trigger})" if skill.trigger else ""
            lines.append(f"  - {name}: {desc}{trigger}")
        return "\n".join(lines)

    def reload(self):
        """Reload all skills from disk."""
        self._skills.clear()
        self._load_skills()


# Skill tool definition
SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "skill",
        "description": "Load and invoke a skill by name. Returns the full skill content with instructions.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The name of the skill to load"
                }
            },
            "required": ["name"]
        }
    }
}


def create_skill_handler(skill_loader: SkillLoader) -> callable:
    """Create skill tool handler."""
    def handle_skill(args: dict) -> str:
        name = args["name"]
        return skill_loader.get_skill_content(name)
    return handle_skill