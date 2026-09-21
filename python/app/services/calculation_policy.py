from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.services.normalization import canonicalize_ingredient_name, clean_text

VALID_CALORIE_POLICIES = {"INCLUDE", "EXCLUDE", "CONDITIONAL"}
VALID_PRICE_POLICIES = {"INCLUDE", "EXCLUDE"}
VALID_MATCH_TYPES = {"EXACT", "REGEX", "EXACT_OR_REGEX"}


def load_calculation_policy(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    defaults = dict(data.get("defaults") or {})
    rules = list(data.get("rules") or [])

    for rule in rules:
        calorie_policy = str(rule.get("calorie_policy") or defaults.get("calorie_policy") or "INCLUDE").upper()
        price_policy = str(rule.get("price_policy") or defaults.get("price_policy") or "INCLUDE").upper()
        match_type = str(rule.get("match_type") or "EXACT").upper()
        if calorie_policy not in VALID_CALORIE_POLICIES:
            raise ValueError(f"invalid calorie_policy={calorie_policy!r} for {rule.get('rule_key')}")
        if price_policy not in VALID_PRICE_POLICIES:
            raise ValueError(f"invalid price_policy={price_policy!r} for {rule.get('rule_key')}")
        if match_type not in VALID_MATCH_TYPES:
            raise ValueError(f"invalid match_type={match_type!r} for {rule.get('rule_key')}")
        if not rule.get("rule_key"):
            raise ValueError("every calculation policy rule needs rule_key")
        if match_type in {"EXACT", "EXACT_OR_REGEX"} and not rule.get("aliases"):
            raise ValueError(f"{rule['rule_key']} needs aliases")
        rule["_normalized_aliases"] = {
            _normalized_name(alias)
            for alias in rule.get("aliases") or []
            if _normalized_name(alias)
        }
        rule["_compiled_regex"] = None
        if match_type in {"REGEX", "EXACT_OR_REGEX"}:
            pattern = rule.get("regex")
            if not pattern:
                raise ValueError(f"{rule['rule_key']} needs regex")
            rule["_compiled_regex"] = re.compile(pattern)
        excluded_patterns = rule.get("exclude_regex") or []
        if isinstance(excluded_patterns, str):
            excluded_patterns = [excluded_patterns]
        rule["_compiled_excludes"] = [re.compile(pattern) for pattern in excluded_patterns]

    data["rules"] = sorted(rules, key=lambda item: int(item.get("priority", 0)), reverse=True)
    data["defaults"] = defaults
    return data


def _normalized_name(name: str) -> str:
    return canonicalize_ingredient_name(clean_text(name))


def _rule_matches(name: str, rule: dict[str, Any]) -> bool:
    match_type = str(rule.get("match_type") or "EXACT").upper()
    aliases = rule.get("_normalized_aliases") or set()

    exact_match = name in aliases if aliases else False
    compiled_regex = rule.get("_compiled_regex")
    regex_match = bool(compiled_regex.search(name)) if compiled_regex else False

    if any(pattern.search(name) for pattern in rule.get("_compiled_excludes") or []):
        return False

    if match_type == "EXACT":
        return exact_match
    if match_type == "REGEX":
        return regex_match
    if match_type == "EXACT_OR_REGEX":
        return exact_match or regex_match
    return False


def classify_ingredient(name: str, policy_data: dict[str, Any]) -> dict[str, Any]:
    """Return the first matched policy rule for a normalized ingredient name.

    A return value is always produced. If no explicit policy matches, project defaults
    are returned with ``matched=False``. Only matched policies are persisted to MySQL;
    unmatched ingredients naturally use INCLUDE/INCLUDE in the calorie-price query.
    """
    normalized = _normalized_name(name)
    defaults = dict(policy_data.get("defaults") or {})

    for rule in policy_data.get("rules") or []:
        if not _rule_matches(normalized, rule):
            continue
        return {
            "matched": True,
            "ingredient_name": normalized,
            "rule_key": str(rule["rule_key"]),
            "ingredient_category": str(rule.get("ingredient_category") or defaults.get("ingredient_category") or "FOOD"),
            "is_seasoning": bool(rule.get("is_seasoning", defaults.get("is_seasoning", False))),
            "calorie_policy": str(rule.get("calorie_policy") or defaults.get("calorie_policy") or "INCLUDE").upper(),
            "price_policy": str(rule.get("price_policy") or defaults.get("price_policy") or "INCLUDE").upper(),
            "calorie_ignore_threshold_kcal": float(rule.get("calorie_ignore_threshold_kcal", defaults.get("calorie_ignore_threshold_kcal", 5.0))),
            "fallback_kcal_per_100g": None if rule.get("fallback_kcal_per_100g") is None else float(rule["fallback_kcal_per_100g"]),
            "confidence_score": float(rule.get("confidence_score", defaults.get("confidence_score", 100))),
            "match_reason": f"{rule.get('match_type', 'EXACT')}:{rule['rule_key']}",
            "note": rule.get("note"),
        }

    return {
        "matched": False,
        "ingredient_name": normalized,
        "rule_key": "DEFAULT_FOOD",
        "ingredient_category": str(defaults.get("ingredient_category") or "FOOD"),
        "is_seasoning": bool(defaults.get("is_seasoning", False)),
        "calorie_policy": str(defaults.get("calorie_policy") or "INCLUDE").upper(),
        "price_policy": str(defaults.get("price_policy") or "INCLUDE").upper(),
        "calorie_ignore_threshold_kcal": float(defaults.get("calorie_ignore_threshold_kcal", 5.0)),
        "fallback_kcal_per_100g": None,
        "confidence_score": float(defaults.get("confidence_score", 100)),
        "match_reason": "DEFAULT",
        "note": None,
    }
