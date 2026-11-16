---
title: "Skill: research"
type: antigravity-skill
scope: project
project: "fardeen"
workspace_dir: "/home/fardeen"
tags:
  - antigravity/skill
  - antigravity/skill/project
updated: 2026-11-13
---

# Skill: research (Proyecto: fardeen)

> [!INFO] **Metadatos de la Skill**
> - **Nombre**: `research`
> - **Proyecto**: `fardeen`
> - **Carpeta Local**: `/home/fardeen`
> - **Archivo Fuente**: `/home/fardeen/.agents/skills/research/SKILL.md`
> - **Descripción**: Investigate a question against high-trust primary sources and capture the findings as a Markdown file in the repo. Use when the user wants a topic researched, docs or API facts gathered, or reading legwork delegated to a background agent.

## Instrucciones del Agente

Spin up a **background agent** to do the research, so you keep working while it reads.

Its job:

1. Investigate the question against **primary sources** (official docs, source code, specs, first-party APIs), not a secondary write-up of them. Follow every claim back to the source that owns it.
2. Write the findings to a single Markdown file, citing each claim's source.
3. Save it where the repo already keeps such notes; match the existing convention, and if there is none, put it somewhere sensible and say where.

---
*Conexiones del Grafo:* [[00 Antigravity Hub]] | [[00 Indice de Skills]] | [[Proyecto: fardeen]]
