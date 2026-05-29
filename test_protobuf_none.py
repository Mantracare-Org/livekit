from livekit import api
try:
    info = api.SIPOutboundTrunkInfo(destination_country=None)
    print("None allowed")
except Exception as e:
    print("None NOT allowed:", e)
