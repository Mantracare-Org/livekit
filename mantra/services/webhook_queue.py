""" Background worker that reliably delivers offloaded webhooks. """

from mantra.utils import send_to_backend
import asyncio
import json
import os
import redis.asyncio as redis

import logging

logger = logging.getLogger("mantra.webhook_queue")

async def process_pending_webhooks():
    """Background worker to reliably deliver webhooks offloaded by the agent."""
    import redis.asyncio as redis
    from redis.exceptions import (
        TimeoutError as RedisTimeoutError,
        ConnectionError as RedisConnectionError,
        ReadOnlyError,
        ResponseError,
    )

    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        logger.warning("No REDIS_URL configured; webhook worker will not start.")
        return

    logger.info("Starting background webhook worker...")
    client = None

    while True:
        try:
            if client is None:
                client = redis.from_url(
                    redis_url,
                    decode_responses=True,
                    socket_timeout=15,
                    socket_connect_timeout=5,
                    health_check_interval=15,
                    retry_on_timeout=True,
                )

            # blpop blocks for up to 5 seconds waiting for a payload
            result = await client.blpop("mantra:pending_webhooks", timeout=5)
            if result:
                _, payload_bytes = result
                try:
                    payload = json.loads(payload_bytes)
                    call_id = payload.get("data", {}).get("call_id", "unknown")
                    logger.info(f"Dequeued webhook for call {call_id}. Delivering to backend...")
                    delivered = await send_to_backend(payload)
                    if delivered:
                        logger.info(f"Successfully delivered offloaded webhook for call {call_id}.")
                    else:
                        logger.warning(f"Webhook delivery for call {call_id} failed, but claim was processed.")
                except json.JSONDecodeError:
                    logger.error("Failed to decode webhook payload from Redis queue.")
                except Exception as ex:
                    logger.error(f"Error processing queued webhook: {ex}", exc_info=True)
        except asyncio.CancelledError:
            logger.info("Webhook worker cancelled. Shutting down.")
            if client:
                try:
                    await client.aclose()
                except Exception:
                    pass
            break
        except (RedisTimeoutError, TimeoutError):
            # Normal timeout when there are no new messages and blpop returns empty
            continue
        except (ReadOnlyError, RedisConnectionError) as e:
            logger.warning(f"Redis connection/replica state error in webhook worker: {e}. Reconnecting in 5s...")
            if client:
                try:
                    await client.aclose()
                except Exception:
                    pass
                client = None
            await asyncio.sleep(5)
        except ResponseError as e:
            if "read only" in str(e).lower() or "unblocked" in str(e).lower():
                logger.warning(f"Redis failover detected in webhook worker ({e}). Reconnecting in 5s...")
            else:
                logger.error(f"Redis response error in webhook worker: {e}. Retrying in 5s...")
            if client:
                try:
                    await client.aclose()
                except Exception:
                    pass
                client = None
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Redis error in webhook worker: {e}. Retrying in 5s...")
            if client:
                try:
                    await client.aclose()
                except Exception:
                    pass
                client = None
            await asyncio.sleep(5)

