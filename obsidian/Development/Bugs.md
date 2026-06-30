# Bugs

## Open

| ID | Description | Module | Severity | Status |
|----|-------------|--------|----------|--------|
| — | None formally tracked | — | — | — |

## Known Issues (from `.planning/codebase/CONCERNS.md`)

| Issue | Impact | Notes |
|-------|--------|-------|
| Zombie calls | Medium | Dispatcher has cleanup but race conditions possible |
| Webhook reliability | Medium | External n8n may be unreachable |
| Monolithic agent.py | Medium | 995 lines, hard to maintain |
| No automated tests | High | Regression risk |
| OpenTelemetry suppression | Low | Hides metrics but prevents 429 errors |
