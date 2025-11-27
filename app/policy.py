"""A small allow/deny policy engine.

Rules are matched against a tool name using shell-glob patterns (``orders.*``,
``*.read``, exact names like ``get_products``). The engine is **secure by
default**: if no rule matches a tool for a given agent, the call is denied.

Precedence (highest wins):
  1. Higher ``priority`` before lower -- this is the primary override knob,
     so an admin can write a single high-priority global "kill switch" rule
     that overrides every agent-specific allow, e.g. to lock down a tool
     mid-incident without touching every agent's rule set.
  2. Agent-scoped rules before global (``agent_id is None``) rules, when
     priority is tied -- a specific grant/restriction for one agent beats a
     same-priority blanket rule.
  3. ``deny`` before ``allow`` when still tied (fail closed).
"""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase

from app.models import PolicyRule


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    matched_rule_id: str | None = None


def _sort_key(rule: PolicyRule) -> tuple[int, int, int]:
    is_agent_scoped = 1 if rule.agent_id is not None else 0
    is_deny = 1 if rule.effect == "deny" else 0
    return (rule.priority, is_agent_scoped, is_deny)


def evaluate(rules: list[PolicyRule], tool_name: str) -> PolicyDecision:
    matches = [r for r in rules if fnmatchcase(tool_name, r.tool_pattern)]

    if not matches:
        return PolicyDecision(
            allowed=False,
            reason=f"no policy rule matches tool '{tool_name}' (default deny)",
        )

    matches.sort(key=_sort_key, reverse=True)
    winner = matches[0]

    if winner.effect == "allow":
        return PolicyDecision(
            allowed=True,
            reason=f"allowed by rule '{winner.tool_pattern}' (priority={winner.priority})",
            matched_rule_id=winner.id,
        )
    return PolicyDecision(
        allowed=False,
        reason=f"denied by rule '{winner.tool_pattern}' (priority={winner.priority})",
        matched_rule_id=winner.id,
    )
