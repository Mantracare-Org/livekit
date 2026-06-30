#!/bin/bash
# Pre-commit hook: blocks commit if source files changed without vault updates
# Called from .git/hooks/pre-commit

STAGED=$(git diff --cached --name-only)
if [ -z "$STAGED" ]; then
  exit 0
fi

STALE_DOCS=""

while IFS= read -r file; do
  case "$file" in
    mantra/agent.py)
      STALE_DOCS="$STALE_DOCS  obsidian/Features/Voice Agent.md\n  obsidian/Architecture/Components.md"
      ;;
    mantra/ui_server.py)
      STALE_DOCS="$STALE_DOCS  obsidian/Features/API Server.md\n  obsidian/Architecture/APIs.md\n  obsidian/Features/Telephony Integration.md"
      ;;
    mantra/dispatcher.py)
      STALE_DOCS="$STALE_DOCS  obsidian/Features/Dispatcher.md"
      ;;
    mantra/utils.py)
      STALE_DOCS="$STALE_DOCS  obsidian/Features/Post-Call Processing.md"
      ;;
    mantra/email_alerts.py)
      STALE_DOCS="$STALE_DOCS  obsidian/Features/Crash Alerts.md"
      ;;
    mcp/server.py)
      STALE_DOCS="$STALE_DOCS  obsidian/Features/MCP Server.md"
      ;;
    static/*.html|static/*.js)
      STALE_DOCS="$STALE_DOCS  obsidian/Features/Dashboard.md\n  obsidian/Features/Test Console.md"
      ;;
    pyproject.toml|Dockerfile|entrypoint.sh)
      STALE_DOCS="$STALE_DOCS  obsidian/Architecture/Dependencies.md"
      ;;
    obsidian/*)
      # vault changes are not stale
      ;;
  esac
done <<< "$STAGED"

if [ -n "$STALE_DOCS" ]; then
  # Check if corresponding vault docs are also staged
  STALE_DOCS_FILTERED=""
  while IFS= read -r doc; do
    doc=$(echo "$doc" | xargs)  # trim
    [ -z "$doc" ] && continue
    if ! echo "$STAGED" | grep -q "$doc"; then
      STALE_DOCS_FILTERED="$STALE_DOCS_FILTERED  - $doc\n"
    fi
  done <<< "$(echo -e "$STALE_DOCS")"

  if [ -n "$STALE_DOCS_FILTERED" ]; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo " ❌ COMMIT BLOCKED: Obsidian vault docs are stale"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo " These vault files need updating for the staged changes:"
    echo ""
    echo -e "$STALE_DOCS_FILTERED"
    echo ""
    echo " Fix: update the docs, git add them, then commit again."
    echo " Skip: git commit --no-verify (not recommended)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    exit 1
  fi
fi

exit 0
