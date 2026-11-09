"""Adapters for each coding-agent CLI, in display order."""
from .base import Agent, OpError
from .claude import Claude
from .codex import Codex
from .copilot import Copilot
from .gemini import Gemini
from .opencode import OpenCode

AGENTS = [Claude(), Codex(), Gemini(), OpenCode(), Copilot()]
BY_NAME = {a.name: a for a in AGENTS}

__all__ = ["AGENTS", "BY_NAME", "Agent", "OpError"]
