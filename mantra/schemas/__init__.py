""" Pydantic schemas for request validation and OpenAPI / Swagger documentation. """

from mantra.schemas.sip import (
    InboundPromptUpdateRequest,
    InboundSipSetupRequest,
    TestInboundCallRequest,
    CreateInboundTrunkRequest,
    CreateDispatchRuleRequest,
    UpdateInboundTrunkRequest,
    UpdateDispatchRuleRequest,
    CreateZadarmaTrunkRequest,
    CreateTwilioTrunkRequest,
    CreateVoiceLinkTrunkRequest,
    PlivoOutboundCallRequest,
)
from mantra.schemas.auth import LoginRequest
from mantra.schemas.org_configs import OrgConfigUpdateRequest
from mantra.schemas.telephony import DispatchTestRequest
from mantra.schemas.knowledge import KBChatRequest, KBTextRequest, KBUrlRequest

__all__ = [
    "InboundPromptUpdateRequest",
    "InboundSipSetupRequest",
    "TestInboundCallRequest",
    "CreateInboundTrunkRequest",
    "CreateDispatchRuleRequest",
    "UpdateInboundTrunkRequest",
    "UpdateDispatchRuleRequest",
    "CreateZadarmaTrunkRequest",
    "CreateTwilioTrunkRequest",
    "CreateVoiceLinkTrunkRequest",
    "PlivoOutboundCallRequest",
    "LoginRequest",
    "OrgConfigUpdateRequest",
    "DispatchTestRequest",
    "KBChatRequest",
    "KBTextRequest",
    "KBUrlRequest",
]
