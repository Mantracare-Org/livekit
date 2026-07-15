# Agent Memory

This repo has a permanent knowledge base at `obsidian/`. Before modifying code:

1. **Read `obsidian/Home.md`** — project overview and navigation
2. **Read `obsidian/Architecture/Overview.md`** — system design
3. **Read `obsidian/Development/Current Sprint.md`** — what's in progress
4. **Read `obsidian/Development/TODO.md`** — pending work
5. **Read `obsidian/Knowledge/Conventions.md`** — code conventions

After completing work, **update**:
- `obsidian/Development/Changelog.md`
- `obsidian/Development/Current Sprint.md`
- Any relevant feature or architecture docs

The vault (`obsidian/`) is the source of truth. `.planning/codebase/` is legacy — use the vault instead.

After `git pull` or `git merge` updates master, a post-merge hook checks for stale vault docs and prints a reminder. If you see it, update the listed vault files before starting new work.
