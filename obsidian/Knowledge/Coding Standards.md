# Coding Standards

## Python

- **Python 3.11+** required (managed via `uv`)
- Async/await throughout (no sync I/O in hot paths)
- Type hints: moderate usage encouraged for function signatures
- Logging: module-level logger (`logger = logging.getLogger("mantra.{module}")`)
- Error handling: `try/except Exception as e:` around network boundaries, log with `traceback.format_exc()`

## Frontend

- **Vanilla HTML/CSS/JS** — no frameworks, no build step
- Use CSS variables for theming (see dashboard design)
- Dark theme ("OpsCraft" — Discord/Linear inspired)
- No external CSS libraries

## General

- Keep functions focused and under 100 lines where possible
- Prefer async context managers for resource cleanup
- All API keys and secrets in `.env.local` (gitignored)
- No secrets in code or logs
