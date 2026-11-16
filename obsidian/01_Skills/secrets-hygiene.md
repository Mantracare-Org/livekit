---
skill: secrets-hygiene
version: 1.0
last_verified: 2026-11-13
triggers: ["secrets-hygiene","/project"]
---

## Procedimiento Operativo
del Agente

# Secrets Hygiene

Hard rule for `assist-ai`: **real credentials never enter git** — not in compose files, config defaults, docs, skills, tests, or commit messages.

This skill is the gate before staging, committing, pushing, or documenting deployment. Pair with `security-and-hardening` for broader OWASP work; this skill is only about credential leakage.

## When to Apply

- Editing `docker-compose*.yml`, `Dockerfile*`, `.env*`, `backend/app/core/config.py`
- Writing or updating deployment / ops docs or agent skills that show env examples
- Adding provider integrations (OpenAI, Jev, Stripe, Twilio, MSG91, Assist, etc.)
- Before `git commit` / `git push` / opening a PR
- After discovering an accidental exposure (rotate first, then clean)

## Never Do

1. **Never hardcode** API keys, passwords, tokens, private keys, or webhook secrets in source, YAML, JSON, Markdown, or skills.
2. **Never put real values in `.env.example`** — placeholders only (`change-me-…`, empty, or clearly fake `sk_test_mock`).
3. **Never use production-looking strings as Python/TS defaults** in `os.getenv(..., "real-secret")`.
4. **Never paste live secrets into agent skills, CHECKLIST, README, or chat-committed snippets.**
5. **Never “fix” a leaked secret by only deleting the line** — assume compromise if it reached a remote; rotate first.

## Always Do

### 1. Env var contract

| Location | Allowed content |
|----------|-----------------|
| `.env` (gitignored) | Real secrets for local/prod host |
| `.env.example` | Names + placeholders only |
| `docker-compose.yml` | `${VAR}` / `${VAR:-safe-local-default}` for non-secret local DX |
| `config.py` defaults | Empty string `""` for secrets; obvious local placeholders only for non-prod DX flags |
| Docs / skills | `${JEV_AI_API_KEY}` or `sk_…` redacted examples — never a live key |

Compose pattern:

```yaml
environment:
  - JEV_AI_API_KEY=${JEV_AI_API_KEY}
  - MA_SERVICE_SECRET=${MA_SERVICE_SECRET}
  - SECRET_KEY=${SECRET_KEY}
env_file:
  - path: .env
    required: false
```

Config pattern:

```python
JEV_AI_API_KEY: str = os.getenv("JEV_AI_API_KEY", "")
MA_SERVICE_SECRET: str = os.getenv("MA_SERVICE_SECRET", "")
HMAC_SECRET: str = os.getenv("HMAC_SECRET", "")
```

### 2. Pre-commit scan (mandatory)

Before every commit that touches env, compose, config, or docs:

```bash
# Staged diff only
git diff --cached | grep -iE 'api[_-]?key|secret|token|password|bearer |BEGIN (RSA |OPENSSH )?PRIVATE'

# High-signal provider prefixes (extend when adding vendors)
git diff --cached | grep -EE 'sk_jev-|sk-proj-|sk-ant-|sk_live_|sk_test_[^m]|AIza[0-9A-Za-z_-]{20}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-'
```

If anything matches a **real** credential shape (not an obvious placeholder like `change-me-…`, `your_…`, `mock_…`, empty assignment), **unstage, remove, and rotate**.

### 3. Network exposure check (compose)

For Postgres/Redis and other data planes:

- Prefer `127.0.0.1:HOST:CONTAINER` binds — do not publish DB/cache on `0.0.0.0` unless explicitly required and firewalled.
- Do not commit weak production passwords; local-only defaults must stay local.

### 4. If a secret already leaked

1. **Rotate / revoke** the credential at the provider (Jev, MA, OpenAI, etc.) immediately.
2. Remove it from the working tree (compose → `${VAR}`, config default → `""`, docs → placeholders).
3. Search the tree and skills for copies of the same value.
4. Warn that history still contains it until a deliberate history purge (only with explicit human approval).
5. Do not re-introduce the old value “for convenience” in examples.

## Assist AI secret inventory (keep out of git)

These must come from environment / host `.env` only:

- `JEV_AI_API_KEY`
- `MA_SERVICE_SECRET`
- `HMAC_SECRET`
- `SECRET_KEY` / `JWT_ACCESS_SECRET` / `JWT_SECRET_KEY`
- `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`
- `ASSEMBLYAI_API_KEY`, `ELEVENLABS_API_KEY`
- `MSG91_AUTH_KEY`, `INTERAKT_API_KEY`
- `TWILIO_AUTH_TOKEN`, `TWILIO_ACCOUNT_SID`
- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`
- `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`
- `ASSIST_API_TOKEN`
- `POSTGRES_PASSWORD` (production)

## Definition of Done

A change that touches credentials is done only when:

- [ ] No live secret strings in the diff
- [ ] Compose/docs/skills use `${VAR}` or placeholders
- [ ] Config defaults for secrets are empty (or clearly non-production placeholders)
- [ ] Pre-commit scan above is clean
- [ ] If anything was previously exposed: rotation called out to the human

---
*Conexiones del Grafo:* [[00 Antigravity Hub]] | [[00 Indice de Skills]] | [[Proyecto: assist-ai]]

## Errores Conocidos y Corrección (Aprendidos en Ejecución)
- Sin incidencias iniciales registradas en ejecución.
