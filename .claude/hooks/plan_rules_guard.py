#!/usr/bin/env python3
"""
Claude Code PreToolUse guard for Edit/Write/Bash envelopes from stdin.
Rules come from PLAN_RULES_JSON, active-run contract_root, or plan_dir.
If no rules file exists and no active freeze is running, this exits 0.
Exit 2 means blocked for PreToolUse; exit 0 means allowed or unenforced.
Malformed stdin, missing input keys, or invalid rules JSON fail open.
"""

import fnmatch
import json
import os
import re
import sys


ANCHORS = {
    "plan.md",
    "goal.md",
    "spec.md",
    "conventions.md",
    "gotchas.md",
    "review-brief.md",
    "rules.json",
    "review-pass.json",
    "rules-pass.json",
}

STATE = {
    "test-ledger.json",
    "final-review-pass.json",
    "active-run.json",
}

ALL_CONTRACTS = ANCHORS | STATE
WRITE_TOKEN_RE = re.compile(
    r">>?|sed\s+-i|(^|\s)rm\s|(^|\s)mv\s|(^|\s)cp\s|tee\s|truncate|dd\s"
)


def plan_dir():
    harness_plan_dir = os.environ.get("HARNESS_PLAN_DIR")
    if harness_plan_dir:
        return harness_plan_dir

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    return os.path.join(project_dir, ".plan")


def active_freeze(plan_root):
    active_run_path = os.path.join(plan_root, "active-run.json")
    if not os.path.exists(active_run_path):
        return False, plan_root

    try:
        with open(active_run_path, "r", encoding="utf-8") as active_run_file:
            active_run = json.load(active_run_file)
    except Exception:
        return False, plan_root

    if not isinstance(active_run, dict) or active_run.get("status") != "running":
        return False, plan_root

    contract_root = active_run.get("contract_root", plan_root)
    if not isinstance(contract_root, str) or not contract_root:
        contract_root = plan_root

    return True, contract_root


def resolve_rules_path(plan_root, freeze_active, contract_root):
    env_rules_path = os.environ.get("PLAN_RULES_JSON")
    if env_rules_path:
        return env_rules_path

    if freeze_active:
        return os.path.join(contract_root, "rules.json")

    return os.path.join(plan_root, "rules.json")


def load_rules(rules_path):
    if not os.path.exists(rules_path):
        return None

    try:
        with open(rules_path, "r", encoding="utf-8") as rules_file:
            rules_doc = json.load(rules_file)
    except Exception as exc:
        print("WARN: failed to parse rules file: {}".format(exc), file=sys.stderr)
        return None

    if not isinstance(rules_doc, dict):
        return None

    return rules_doc


def contract_basename(path):
    if not isinstance(path, str):
        return ""
    return os.path.basename(path.rstrip(os.sep))


def _candidate_roots():
    """글로브가 상대 표기이므로, 절대 file_path를 상대화할 레포 루트 후보를 모은다.

    실 CLI는 file_path를 절대경로로 넘기지만 rules.json의 allow_globs/path_glob은
    레포 상대 표기다. 루트 후보는 CLAUDE_PROJECT_DIR과 active-run.json의
    worktree_root(워크트리 루트)·contract_root(.plan)의 부모에서 모은다.
    """
    roots = []
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        roots.append(project_dir)

    active_run_path = os.path.join(plan_dir(), "active-run.json")
    try:
        with open(active_run_path, "r", encoding="utf-8") as active_run_file:
            active_run = json.load(active_run_file)
    except Exception:
        active_run = None

    if isinstance(active_run, dict):
        worktree_root = active_run.get("worktree_root")
        if isinstance(worktree_root, str) and worktree_root:
            roots.append(worktree_root)
        contract_root = active_run.get("contract_root")
        if isinstance(contract_root, str) and contract_root:
            roots.append(os.path.dirname(contract_root.rstrip(os.sep)))

    seen = set()
    unique = []
    for root in roots:
        if root and root not in seen:
            seen.add(root)
            unique.append(root)
    return unique


def match_paths(file_path):
    """원본 file_path와, 레포 루트 후보 기준 상대경로 후보를 함께 반환한다.

    원본(절대)도 후보에 남겨, 상대 envelope를 쓰던 기존 합성 테스트도 통과한다.
    """
    candidates = [file_path]
    if isinstance(file_path, str) and os.path.isabs(file_path):
        for root in _candidate_roots():
            root_norm = root.rstrip(os.sep)
            if file_path.startswith(root_norm + os.sep):
                rel = file_path[len(root_norm) + 1:]
                if rel and rel not in candidates:
                    candidates.append(rel)
    return candidates


def rule_enforces_hook(rule):
    enforce = rule.get("enforce")
    if isinstance(enforce, str):
        return enforce == "hook"
    if isinstance(enforce, list):
        return "hook" in enforce
    return False


def hook_rules(rules_doc):
    if not isinstance(rules_doc, dict):
        return []

    rules = rules_doc.get("rules", [])
    if not isinstance(rules, list):
        return []

    return [rule for rule in rules if isinstance(rule, dict) and rule_enforces_hook(rule)]


def allowed_by_scope(rules_doc, file_path):
    if not isinstance(rules_doc, dict):
        return True

    scope = rules_doc.get("scope")
    if not isinstance(scope, dict):
        return True

    allow_globs = scope.get("allow_globs")
    if not isinstance(allow_globs, list):
        return True

    paths = match_paths(file_path)
    return any(
        isinstance(glob, str) and any(fnmatch.fnmatch(path, glob) for path in paths)
        for glob in allow_globs
    )


def detect_proposed_content(rules_doc, file_path, content):
    for rule in hook_rules(rules_doc):
        detect = rule.get("detect")
        if not isinstance(detect, dict):
            continue

        if detect.get("phase") != "proposed_content":
            continue

        path_glob = detect.get("path_glob")
        forbid_regex = detect.get("forbid_regex")
        if not isinstance(path_glob, str) or not isinstance(forbid_regex, str):
            continue

        if not any(fnmatch.fnmatch(path, path_glob) for path in match_paths(file_path)):
            continue

        try:
            matched = re.search(forbid_regex, content)
        except re.error as exc:
            print(
                "WARN(detect:{}): invalid forbid_regex: {}".format(rule.get("id", ""), exc),
                file=sys.stderr,
            )
            continue

        if matched:
            print(
                "BLOCK(detect:{}): {}".format(rule.get("id", ""), forbid_regex),
                file=sys.stderr,
            )
            return 2

    return 0


def detect_command(rules_doc, command):
    for rule in hook_rules(rules_doc):
        detect = rule.get("detect")
        if not isinstance(detect, dict):
            continue

        if detect.get("phase") != "command":
            continue

        forbid_regex = detect.get("forbid_regex")
        if not isinstance(forbid_regex, str):
            continue

        try:
            matched = re.search(forbid_regex, command)
        except re.error as exc:
            print(
                "WARN(detect:{}): invalid forbid_regex: {}".format(rule.get("id", ""), exc),
                file=sys.stderr,
            )
            continue

        if matched:
            print(
                "BLOCK(detect:{}): {}".format(rule.get("id", ""), forbid_regex),
                file=sys.stderr,
            )
            return 2

    return 0


def handle_edit_write(tool_name, tool_input, freeze_active, rules_doc):
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str):
        return 0

    if tool_name == "Write":
        content = tool_input.get("content", "")
    else:
        content = tool_input.get("new_string", "")
    if not isinstance(content, str):
        content = ""

    name = contract_basename(file_path)
    if freeze_active and name in ALL_CONTRACTS:
        print(
            "BLOCK(freeze): {} is a frozen contract (use contract_writer for state)".format(
                name
            ),
            file=sys.stderr,
        )
        return 2

    if not allowed_by_scope(rules_doc, file_path):
        print("BLOCK(scope): {} not in allow_globs".format(file_path), file=sys.stderr)
        return 2

    return detect_proposed_content(rules_doc, file_path, content)


def handle_bash(tool_input, freeze_active, rules_doc):
    command = tool_input.get("command")
    if not isinstance(command, str):
        return 0

    if (
        freeze_active
        and "contract_writer" not in command
        and WRITE_TOKEN_RE.search(command)
    ):
        for name in sorted(ALL_CONTRACTS):
            if name in command:
                print(
                    "BLOCK(freeze-bash): raw write to contract {}".format(name),
                    file=sys.stderr,
                )
                return 2

    return detect_command(rules_doc, command)


def main():
    try:
        envelope = json.load(sys.stdin)
    except Exception:
        return 0

    if not isinstance(envelope, dict):
        return 0

    tool_name = envelope.get("tool_name")
    if tool_name not in ("Edit", "Write", "Bash"):
        return 0

    tool_input = envelope.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0

    plan_root = plan_dir()
    freeze_active, contract_root = active_freeze(plan_root)
    rules_path = resolve_rules_path(plan_root, freeze_active, contract_root)

    if not freeze_active and not os.path.exists(rules_path):
        return 0

    rules_doc = load_rules(rules_path)

    if tool_name in ("Edit", "Write"):
        return handle_edit_write(tool_name, tool_input, freeze_active, rules_doc)

    if tool_name == "Bash":
        return handle_bash(tool_input, freeze_active, rules_doc)

    return 0


if __name__ == "__main__":
    sys.exit(main())
