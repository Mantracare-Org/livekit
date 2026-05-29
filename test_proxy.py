import asyncio
import aiohttp

async def main():
    proxy = "http://15.206.0.235:8888"
    async with aiohttp.ClientSession(proxy=proxy) as session:
        # Check our IP address via ipify
        async with session.get("https://api.ipify.org?format=json") as resp:
            data = await resp.json()
            print("IP with session(proxy=...):", data)

    # Now let's try passing it to the request directly
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.ipify.org?format=json", proxy=proxy) as resp:
            data = await resp.json()
            print("IP with get(proxy=...):", data)

asyncio.run(main())
