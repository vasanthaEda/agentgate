"""Pure unit tests for the allow/deny policy engine (no DB/HTTP involved)."""
from __future__ import annotations

from app.models import PolicyRule
from app.policy import evaluate


def _rule(agent_id, tool_pattern, effect, priority=0, rule_id="r1"):
    return PolicyRule(
        id=rule_id,
        agent_id=agent_id,
        tool_pattern=tool_pattern,
        effect=effect,
        priority=priority,
    )


def test_no_rules_means_default_deny():
    decision = evaluate([], "get_product")
    assert decision.allowed is False
    assert "default deny" in decision.reason


def test_global_allow_rule_permits_matching_tool():
    rules = [_rule(None, "get_product", "allow", rule_id="g1")]
    decision = evaluate(rules, "get_product")
    assert decision.allowed is True
    assert decision.matched_rule_id == "g1"


def test_wildcard_pattern_matches():
    rules = [_rule(None, "get_*", "allow", rule_id="g1")]
    assert evaluate(rules, "get_product").allowed is True
    assert evaluate(rules, "create_order").allowed is False


def test_non_matching_tool_is_denied_even_with_other_rules():
    rules = [_rule(None, "get_product", "allow", rule_id="g1")]
    decision = evaluate(rules, "create_order")
    assert decision.allowed is False
    assert decision.matched_rule_id is None


def test_agent_specific_rule_overrides_global_rule_of_equal_priority():
    rules = [
        _rule(None, "create_order", "allow", priority=0, rule_id="global-allow"),
        _rule("agent-1", "create_order", "deny", priority=0, rule_id="agent-deny"),
    ]
    decision = evaluate(rules, "create_order")
    assert decision.allowed is False
    assert decision.matched_rule_id == "agent-deny"


def test_higher_priority_wins_regardless_of_scope():
    rules = [
        _rule("agent-1", "create_order", "allow", priority=1, rule_id="agent-allow-low"),
        _rule(None, "create_order", "deny", priority=10, rule_id="global-deny-high"),
    ]
    decision = evaluate(rules, "create_order")
    assert decision.allowed is False
    assert decision.matched_rule_id == "global-deny-high"


def test_deny_wins_ties_fail_closed():
    rules = [
        _rule(None, "create_order", "allow", priority=5, rule_id="allow-rule"),
        _rule(None, "create_order", "deny", priority=5, rule_id="deny-rule"),
    ]
    decision = evaluate(rules, "create_order")
    assert decision.allowed is False
    assert decision.matched_rule_id == "deny-rule"


def test_most_specific_wildcard_still_just_a_glob_match_last_writer_by_priority():
    # Two overlapping globs: exact-tool deny should be given higher priority
    # explicitly to take precedence over a broad allow (globs don't imply
    # specificity ordering on their own -- priority is the mechanism).
    rules = [
        _rule(None, "orders_*", "allow", priority=0, rule_id="broad-allow"),
        _rule(None, "orders_cancel", "deny", priority=5, rule_id="narrow-deny"),
    ]
    assert evaluate(rules, "orders_cancel").allowed is False
    assert evaluate(rules, "orders_create").allowed is True
