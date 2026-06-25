import asyncio
import os
from livekit import api

async def main():
    lk_client = api.LiveKitAPI(
        url="https://mantraassist-0ek43ife.livekit.cloud",
        api_key="APIuVtPUXrSyKc7",
        api_secret="cbfkc2nd46GXzAaa2L1C1UqsTh5cmnOkaNhqRSJtVPH"
    )
    res = await lk_client.sip.list_outbound_trunk(api.ListSIPOutboundTrunkRequest(trunk_ids=["ST_fqoni9kLPkCz"]))
    for t in res.items:
        print(f"Trunk ID: {t.sip_trunk_id}")
        print(f"Name: {t.name}")
        print(f"Address: {t.address}")
        print(f"Numbers: {t.numbers}")
    await lk_client.aclose()

asyncio.run(main())
