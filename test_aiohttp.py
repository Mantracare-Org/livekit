import aiohttp
import inspect
print("Session args:", inspect.signature(aiohttp.ClientSession.__init__))
