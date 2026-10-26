---
skill: antigravity-obsidian
version: 1.0
last_verified: 2026-10-26
triggers: ["antigravity-obsidian","/global"]
---

## Procedimiento Operativo
del Agente

# Obsidian for Antigravity — Technical Second Brain

Connected Vault: **obsidian** (`/home/fardeen/lkt/obsidian`).

## Decision Matrix: Skill vs Memory vs Nothing

| Type | Purpose | When to Query | Where to Look |
|---|---|---|---|
| **SKILL** | HOW to do it? (Procedures, APIs & Workflows) | When operating specialized tools (e.g. ERP, PrestaShop, PA tokens) | `<skills>` in prompt or `view_file` on its `SKILL.md` |
| **MEMORY** | WHAT was done before? (Past records, Bugs & Architecture) | On unknown errors, environment quirks, or previous decisions | `triage` and `peek` via this script |
| **NOTHING** | Trivial tasks or self-evident code | Greetings, general theory, or self-explanatory code | **Do not query anything (0 tokens)** |

---

## Low-Context Query Protocol

### Step 1: Smart Triage (<80 tokens)
```bash
node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" triage "key terms of problem or project"
```
- **If triage returns a summary:** Use it directly. Solve the problem without reading more files.
- **If triage recommends a Skill:** Activate the specified skill to follow the procedure.
- **If hasAntecedents is false:** Proceed directly without further searches.

### Step 2: Selective Extraction with Peek (Only if summary is insufficient)
```bash
node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" peek "NoteName"
```
*(Extracts strictly the technical solution, saving 90% context vs full read).*

---

## Balanced Autonomous Recording (Milestones, Architecture, Sessions & Learnings)
**Balanced memory policy (50/50 - Neither zero nor noise).**
Antigravity automatically maintains project continuity and architectural awareness without spamming useless noise.

**Record into Second Brain when:**
1. **Finishing a task or session:** Autonomously save a session checkpoint (`node obsidian.js session save ...`).
2. **Opening or modifying project structure:** Re-scan project architecture blueprint (`node obsidian.js project scan ...`).
3. **Discovering repository traps or forbidden rules:** Record anti-patterns (`node obsidian.js project antipattern add ...`).
4. **Resolving non-trivial blockers:** Save technical solution note (`node obsidian.js save ...`).
5. **User habits and preferences:** Persist workflow conditions (`node obsidian.js learn ...`).

**Mandatory Style Rules:**
- **Zero emojis**: No emojis in titles, summaries, or content.
- **Actionable & concise**: Direct technical facts, exact commands and code diffs.

**Key CLI commands:**
```bash
# Record session checkpoint
node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" session save --summary "Brief summary" --content "Details, modified files, state"

# Recall previous session instantly (<50 tokens)
node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" session last

# Scan and update project architecture blueprint
node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" project scan
```

---

## Chat Slash Commands (/obsidian)

Users can run quick actions in chat by typing slash commands:

| Slash Command | Equivalent CLI Execution | Description |
|---|---|---|
| `/obsidian name <name>` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" name "<name>"` | Change AI agent name immediately |
| `/obsidian user <callsign>` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" user "<callsign>"` | Change user callsign/name immediately |
| `/obsidian config [get|set]` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" config [args]` | Query or update configuration |
| `/obsidian` or `/obsidian status` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" status` | Check connection and stats |
| `/obsidian personality [args]` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" personality [args]` | View or configure AI agent personality, name & user callsign |
| `/obsidian session save [args]` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" session save [args]` | Store work session checkpoint |
| `/obsidian session last [project]` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" session last [project]` | Recall previous session context |
| `/obsidian session list [project]` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" session list [project]` | List recent checkpoints |
| `/obsidian project scan [path]` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" project scan [path]` | Re-scan architecture and update blueprint |
| `/obsidian project backlog [args]` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" project backlog [args]` | View or manage tasks |
| `/obsidian project antipattern [args]` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" project antipattern [args]` | Record or view forbidden traps |
| `/obsidian project [register|list|status]` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" project [args]` | Manage unified projects registry & auto-detection |
| `/obsidian save <title> [content]` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" save --title "<title>" --content "<content>"` | Save technical solution |
| `/obsidian skill [list|view|create|edit|delete]` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" skill [args]` | Manage AI agent skills (create, edit, view, delete) |
| `/obsidian rule [list|view|add]` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" rule [args]` | Inspect or register operational rules |
| `/obsidian soul` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" soul` | View active Soul & User Profile |
| `/obsidian learn <preference>` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" learn "<preference>"` | Learn new user workflow habit |
| `/obsidian triage <query>` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" triage "<query>"` | Low-context triage |
| `/obsidian peek <note>` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" peek "<note>"` | Extract exact technical fix |
| `/obsidian catalog` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" catalog` | 1-line overview of skills & memories |
| `/obsidian skills` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" skills` | List available vault skills |
| `/obsidian memories` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" memories` | List technical memories |
| `/obsidian mcp [status|install|disable|enable]` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" mcp [args]` | Manage MCP Server safely without breaking existing MCPs |
| `/obsidian open [note]` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" open [note]` | Open note in Obsidian Desktop |
| `/obsidian help` | `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" help` | Show quick command list |

---

## Available CLI Commands

| Command | Description |
|---|---|
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" mcp [status|install|disable|enable]` | Manage MCP Server safely without breaking existing MCPs |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" name "<name>"` | Change AI agent name immediately |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" user "<callsign>"` | Change user callsign immediately |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" config [get|set ...]` | Query or update configuration |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" personality [--ai-name <n>] [--user-callsign <c>] [--personality <p>]` | View or configure AI personality & callsign |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" session save --summary "..." --content "..."` | Record session checkpoint into Vault |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" session last [--project "..."]` | Recall previous session context in <50 tokens |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" session list [--limit N]` | List recent session checkpoints |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" project scan [path]` | Deep scan architecture and generate project blueprint |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" project register [path]` | Register project with deep structure |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" project antipattern [list|add "<rule>"]` | Manage repository traps and forbidden anti-patterns |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" project backlog [list|add "<task>"|done "<task>"]` | Double-way backlog synchronization with Obsidian |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" skill list [--scope global|project]` | List all installed skills across workspace and global |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" skill view <name>` | Low-context view of skill instructions and helper scripts |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" skill create <name> --desc "<d>" --content "<c>"` | Create standard AI skill & sync with Obsidian |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" skill edit <name> [--desc "<d>"] [--content "<c>"] [--append "<a>"]` | Modify existing skill cleanly |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" skill delete <name>` | Safely remove skill and clean Obsidian note |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" skill script <skill> add <file> --code "..."` | Attach executable script to skill |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" rule list` | List all active global, workspace, and dynamic rules |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" rule view <name>` | Inspect specific rule content |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" triage "query"` | Ultra-compact smart triage (<80 tokens) |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" peek "Note"` | Direct technical solution without metadata overhead |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" save --title "..." ...` | Atomic save to vault and Knowledge Items |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" catalog` | 1-line summary of all skills and memories |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" skills` | Quick list of skills with short descriptions |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" memories` | Quick list of memories with short summaries |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" soul` | Shows active Soul and User Profile |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" learn "text"` | Records a newly learned habit or preference |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" status` | Connection status and vault stats |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" search "query"` | Compact search |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" read "Note"` | Full read (use only if strictly necessary) |
| `node "/home/fardeen/.gemini/config/skills/antigravity-obsidian/scripts/obsidian.js" open [Note]` | Opens note in Obsidian Desktop |

---
*Conexiones del Grafo:* [[00 Antigravity Hub]] | [[00 Indice de Skills]]

## Errores Conocidos y Corrección (Aprendidos en Ejecución)
- Sin incidencias iniciales registradas en ejecución.
