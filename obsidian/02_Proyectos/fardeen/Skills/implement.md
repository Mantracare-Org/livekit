---
title: "Skill: implement"
type: antigravity-skill
scope: project
project: "fardeen"
workspace_dir: "/home/fardeen"
tags:
  - antigravity/skill
  - antigravity/skill/project
updated: 2026-11-13
---

# Skill: implement (Proyecto: fardeen)

> [!INFO] **Metadatos de la Skill**
> - **Nombre**: `implement`
> - **Proyecto**: `fardeen`
> - **Carpeta Local**: `/home/fardeen`
> - **Archivo Fuente**: `/home/fardeen/.agents/skills/implement/SKILL.md`
> - **Descripción**: "Implement a piece of work based on a spec or set of tickets."

## Instrucciones del Agente

Implement the work described by the user in the spec or tickets.

If the user passes a ticket reference, fetch it from the issue tracker and state its title before starting. If the reference is ambiguous, ask.

Call the Skill tool with "tdd" where possible, at pre-agreed seams.

Run typechecking regularly, single test files regularly, and the full test suite once at the end.

Once done, call the Skill tool with "code-review" to review the work.

Commit your work to the current branch.

---
*Conexiones del Grafo:* [[00 Antigravity Hub]] | [[00 Indice de Skills]] | [[Proyecto: fardeen]]
