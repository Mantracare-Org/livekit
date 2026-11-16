---
title: "Skill: handoff"
type: antigravity-skill
scope: project
project: "fardeen"
workspace_dir: "/home/fardeen"
tags:
  - antigravity/skill
  - antigravity/skill/project
updated: 2026-11-13
---

# Skill: handoff (Proyecto: fardeen)

> [!INFO] **Metadatos de la Skill**
> - **Nombre**: `handoff`
> - **Proyecto**: `fardeen`
> - **Carpeta Local**: `/home/fardeen`
> - **Archivo Fuente**: `/home/fardeen/.agents/skills/handoff/SKILL.md`
> - **Descripción**: Compact the current conversation into a handoff document for another agent to pick up.

## Instrucciones del Agente

Write a handoff document summarising the current conversation so a fresh agent can continue the work. Save to the temporary directory of the user's OS (`$TMPDIR`, else `/tmp`; `%TEMP%` on Windows) - not the current workspace.

Include a "suggested skills" section in the document, naming which skills the next agent should call the Skill tool for.

Do not duplicate content already captured in other artifacts (specs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead.

Redact any sensitive information, such as API keys, passwords, or personally identifiable information.

If the user passed arguments, treat them as a description of what the next session will focus on and tailor the doc accordingly.

---
*Conexiones del Grafo:* [[00 Antigravity Hub]] | [[00 Indice de Skills]] | [[Proyecto: fardeen]]
