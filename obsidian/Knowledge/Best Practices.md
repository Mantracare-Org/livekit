# Best Practices

## For This Codebase

1. **Always test manually** after changes (see [[Agents/QA Agent.md]])
2. **Update Obsidian vault** before and after implementation
3. **Keep `.env.local`** out of version control (it's gitignored)
4. **Log at INFO** for production-relevant events, DEBUG for development noise
5. **Use async/await** everywhere — no `time.sleep()` or blocking calls
6. **Strip proxy env vars** before S3 uploads (they break boto3)
7. **Wrap network calls** in try/except with `traceback.format_exc()`
8. **Set TTL** on Redis keys that don't need to persist

## Safety

- Never commit API keys or secrets
- Always use `asyncio.shield()` for critical cleanup code
- Test call limiter and safety net after prompt changes
- Verify HMAC signing when modifying webhook delivery
