#!/bin/bash
# Post-merge hook: detects changed source files and maps them to vault docs
# Called from .git/hooks/post-merge

CHANGED_FILES=$(git diff-tree -r --name-only HEAD ORIG_HEAD 2>/dev/null || true)
if [ -z "$CHANGED_FILES" ]; then
  exit 0
fi

HAS_CODE_CHANGES=false
STALE_DOCS=""

while IFS= read -r file; do
  case "$file" in
    mantra/agent.py)
      STALE_DOCS="$STALE_DOCS  - obsidian/Features/Voice Agent.md\n  - obsidian/Architecture/Components.md\n  - obsidian/Development/Changelog.md"
      HAS_CODE_CHANGES=true
      ;;
    mantra/ui_server.py)
      STALE_DOCS="$STALE_DOCS  - obsidian/Features/API Server.md\n  - obsidian/Features/Telephony Integration.md\n  - obsidian/Architecture/APIs.md\n  - obsidian/Development/Changelog.md"
      HAS_CODE_CHANGES=true
      ;;
    mantra/dispatcher.py)
      STALE_DOCS="$STALE_DOCS  - obsidian/Features/Dispatcher.md\n  - obsidian/Development/Changelog.md"
      HAS_CODE_CHANGES=true
      ;;
    mantra/utils.py)
      STALE_DOCS="$STALE_DOCS  - obsidian/Features/Post-Call Processing.md\n  - obsidian/Development/Changelog.md"
      HAS_CODE_CHANGES=true
      ;;
    mantra/email_alerts.py)
      STALE_DOCS="$STALE_DOCS  - obsidian/Features/Crash Alerts.md\n  - obsidian/Development/Changelog.md"
      HAS_CODE_CHANGES=true
      ;;
    mcp/server.py)
      STALE_DOCS="$STALE_DOCS  - obsidian/Features/MCP Server.md\n  - obsidian/Development/Changelog.md"
      HAS_CODE_CHANGES=true
      ;;
    static/*.html|static/*.js)
      STALE_DOCS="$STALE_DOCS  - obsidian/Features/Dashboard.md\n  - obsidian/Features/Test Console.md\n  - obsidian/Development/Changelog.md"
      HAS_CODE_CHANGES=true
      ;;
    pyproject.toml|Dockerfile|entrypoint.sh)
      STALE_DOCS="$STALE_DOCS  - obsidian/Architecture/Dependencies.md\n  - obsidian/Development/Changelog.md"
      HAS_CODE_CHANGES=true
      ;;
    obsidian/*)
      # vault changes are not stale
      ;;
  esac
done <<< "$CHANGED_FILES"

if [ "$HAS_CODE_CHANGES" = true ]; then
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo " ⚠ OBSIDIAN VAULT MAY BE STALE"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo -e "$STALE_DOCS" | sort -u
  echo ""
  echo " Run the agent to review and update docs."
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
fi
