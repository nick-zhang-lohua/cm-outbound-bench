"""Blind LLM-judge scoring of Group A outputs against the frozen Gold Labels.

The judge is DeepSeek V4 Pro, separate from the evaluated Group A agent
(deepseek-flash). Scoring can never change the frozen Ground Truth: the gold
references are opened read-only and the scorer only produces an outcome record.
"""

from __future__ import annotations

__all__ = []
