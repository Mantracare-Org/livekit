#!/bin/bash
# Install git hooks for this repo
set -e

REPO_DIR="$(git rev-parse --show-toplevel)"
HOOK_DIR="$(git rev-parse --git-dir)"

echo "Installing hooks..."

# Pre-commit vault guard
cat > "$HOOK_DIR/hooks/pre-commit" << 'HOOK'
#!/bin/bash
scripts/pre-commit-vault-guard.sh
HOOK
chmod +x "$HOOK_DIR/hooks/pre-commit"

# Post-merge stale check
cat > "$HOOK_DIR/hooks/post-merge" << 'HOOK'
#!/bin/bash
scripts/vault-stale-check.sh
HOOK
chmod +x "$HOOK_DIR/hooks/post-merge"

# Merge driver for Obsidian user config
git config merge.ours.driver true

echo "Done. Hooks installed:"
echo "  - pre-commit: blocks commit if vault docs are stale"
echo "  - post-merge: warns if vault docs may be stale after merge"
echo "  - merge.ours.driver: protects .obsidian/graph.json and workspace.json"
