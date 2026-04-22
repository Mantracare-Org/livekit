"""
Quick test: Create a room and explicitly dispatch the agent to it.
This bypasses auto-dispatch and tells us if the agent worker is healthy.
"""
import asyncio
import os
from dotenv import load_dotenv
from livekit import api

load_dotenv(".env.local")

async def main():
    lkapi = api.LiveKitAPI(
        url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )

    # 1. List existing rooms
    print("=== Listing rooms ===")
    rooms = await lkapi.room.list_rooms(api.ListRoomsRequest())
    for r in rooms.rooms:
        print(f"  Room: {r.name}, participants: {r.num_participants}")

    # 2. Create/ensure test room
    room_name = "test-dispatch-room"
    print(f"\n=== Creating room '{room_name}' ===")
    room = await lkapi.room.create_room(api.CreateRoomRequest(name=room_name))
    print(f"  Room created: {room.name}, sid: {room.sid}")

    # 3. Create an explicit agent dispatch
    print(f"\n=== Dispatching agent to '{room_name}' ===")
    try:
        dispatch = await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(room=room_name)
        )
        print(f"  ✅ Dispatch created: {dispatch}")
    except Exception as e:
        print(f"  ❌ Dispatch failed: {e}")
        print("  This usually means agent dispatch is not enabled on your LiveKit Cloud project.")

    # 4. Generate a join token for browser testing
    token = api.AccessToken(os.getenv("LIVEKIT_API_KEY"), os.getenv("LIVEKIT_API_SECRET")) \
        .with_identity("TestUser") \
        .with_name("Test User") \
        .with_grants(api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
        ))
    print(f"\n=== Join Token ===")
    print(f"  Room: {room_name}")
    print(f"  Token: {token.to_jwt()}")
    print(f"\n  Test URL: https://agents-playground.livekit.io/#/custom?liveKitUrl={os.getenv('LIVEKIT_URL')}&token={token.to_jwt()}")

    await lkapi.aclose()

asyncio.run(main())
