---
title: "Skill: ui-skills"
type: antigravity-skill
scope: project
project: "assist-monorepo"
workspace_dir: "/home/fardeen/assist-monorepo"
tags:
  - antigravity/skill
  - antigravity/skill/project
updated: 2026-11-13
---

# Skill: ui-skills (Proyecto: assist-monorepo)

> [!INFO] **Metadatos de la Skill**
> - **Nombre**: `ui-skills`
> - **Proyecto**: `assist-monorepo`
> - **Carpeta Local**: `/home/fardeen/assist-monorepo`
> - **Archivo Fuente**: `/home/fardeen/assist-monorepo/.agents/skills/ui-skills/SKILL.md`
> - **Descripción**: Use before UI-related work to select the smallest useful UI Skills context through the ui-skills CLI.

## Instrucciones del Agente

# UI Skills Root

You are the routing layer for UI Skills.

This skill is shown by `npx ui-skills start` and is also available in the registry.

Use it when an agent in Codex, Cursor, Claude Code, or Antigravity has a clear UI goal.

If the goal is unclear, ask one short question.

If the goal is clear, choose the right category, load the smallest useful skill context, then implement.

## Protocol

1. decide if the task is UI-related
2. if not, return `no skill needed`
3. identify the likely category
4. inspect that category with the CLI
5. select the smallest useful skill set
6. load only selected skill(s)
7. implement using that context

## CLI Commands

```bash
npx ui-skills start
npx ui-skills categories
npx ui-skills list --category <category>
npx ui-skills get <slug>
```

## Selection Rules

- Prefer 1 skill.
- Use 2 only when the task needs two clear angles.
- Use 3 only for broad review, redesign, or multi-surface work.
- Never use more than 3.
- Route by topic, then stack, then specificity.
- Prefer specific skills over broad skills.
- Prefer framework-specific skills when the stack is obvious.
- For quick cleanup, prefer the most specific craft, visual, or layout skill available (e.g. `npx ui-skills get baseline-ui`).
- If unsure, inspect categories and pick the safest narrow skill.

## Available Categories

- `accessibility`
- `motion`
- `systems`
- `visual`
- `interaction`
- `performance`
- `craft`
- `taste`
- `typography`
- `color`
- `frontend`
- `architecture`
- `testing`
- `debugging`
- `code-quality`
- `tooling`
- `nextjs`
- `frameworks`
- `shadcn-ui-development`

---
*Conexiones del Grafo:* [[00 Antigravity Hub]] | [[00 Indice de Skills]] | [[Proyecto: assist-monorepo]]
