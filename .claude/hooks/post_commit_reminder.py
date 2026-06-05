"""PostToolUse(Bash) hook — emits a CLAUDE.md-update reminder only after a
git commit, instead of after every Write/Edit. Reads the tool-use JSON from
stdin; prints the reminder to stdout (which Claude Code injects as a
system-reminder); stays silent on every other Bash command.

Wired in .claude/settings.json. Safe — any exception fails silently so a hook
glitch can never break a normal Bash call.
"""
import sys
import json


def _is_commit(cmd: str) -> bool:
    # Match `git commit`, `git commit -m`, `git commit -am`, `git commit -F`,
    # `git commit --amend`, etc. Exclude `--dry-run` (no actual commit).
    if "git commit" not in cmd:
        return False
    if "--dry-run" in cmd:
        return False
    return True


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # No payload → nothing to do.

    tool = payload.get("tool_name") or ""
    cmd = (payload.get("tool_input") or {}).get("command") or ""

    if tool != "Bash" or not _is_commit(cmd):
        return 0

    print(
        "Just committed. If this commit affected commands/workflows, architecture, "
        "backend modules/APIs, configuration, dependencies, or project structure, "
        "update CLAUDE.md (in this commit or a follow-up). Skip if the change was "
        "tests-only or scratch-only."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
