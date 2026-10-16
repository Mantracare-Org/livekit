from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator


class InboundPromptUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    prompt: str = Field(
        ...,
        description="Agent system prompt text",
    )
    org_id: Union[str, int] = Field(
        ...,
        description="Target organization ID",
    )
    phone_number: Optional[str] = Field(
        None,
        description="Target phone number in E.164 format",
    )
    number: Optional[str] = Field(
        None,
        description="Alias for phone_number",
    )
    phone: Optional[str] = Field(
        None,
        description="Alias for phone_number",
    )
    sip_trunk_id: Optional[str] = Field(
        None,
        description="Target SIP trunk ID",
    )
    dispatch_rule_id: Optional[str] = Field(
        None,
        description="Target dispatch rule ID",
    )
    voice: Optional[str] = Field(
        None,
        description="Agent voice name",
    )
    model: Optional[str] = Field(
        None,
        description="LLM model identifier",
    )

    @model_validator(mode="after")
    def validate_phone_number(self):
        if not (self.phone_number or self.number or self.phone):
            raise ValueError("'phone_number' (or 'number') is required")
        return self


class InboundSipSetupRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    number: str = Field(
        ...,
        description="Phone number in E.164 format",
    )
    org_id: Union[str, int] = Field(
        ...,
        description="Target organization ID",
    )
    provider: str = Field(
        ...,
        description="Telephony provider name",
    )
    name: Optional[str] = Field(
        None,
        description="Trunk display name",
    )
    prompt: Optional[str] = Field(
        None,
        description="Inbound agent system prompt",
    )
    voice: Optional[str] = Field(
        None,
        description="Agent voice identifier",
    )
    model: Optional[str] = Field(
        None,
        description="Agent LLM model",
    )
    kb_tags: Optional[List[str]] = Field(
        None,
        description="Knowledge base tags list",
    )
    client_name: Optional[str] = Field(None, description="Client name")
    process_id: Optional[str] = Field(None, description="Process ID")


class TestInboundCallRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    phone: str = Field(
        ...,
        description="Destination phone number to receive simulated call",
    )
    trunk_id: str = Field(
        ...,
        description="SIP trunk ID to initiate call through",
    )
    country_code: Optional[str] = Field(
        None,
        description="Country code if phone is not in E.164 format",
    )
    prompt: Optional[str] = Field(
        None,
        description="Agent prompt for test call",
    )
    agent_name: Optional[str] = Field(None, description="LiveKit agent name to dispatch")
    org_id: Optional[Union[str, int]] = Field(None, description="Organization ID")


class CreateInboundTrunkRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(
        ...,
        description="SIP inbound trunk name",
    )
    numbers: List[str] = Field(
        ...,
        description="Phone numbers associated with trunk",
    )
    auth_username: Optional[str] = Field(None, description="SIP auth username")
    auth_password: Optional[str] = Field(None, description="SIP auth password")


class CreateDispatchRuleRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    trunk_id: str = Field(
        ...,
        description="Target SIP trunk ID to bind rule to",
    )
    room_prefix: Optional[str] = Field("inbound_", description="Prefix for room names")
    name: Optional[str] = Field(None, description="Dispatch rule name")
    phone_number: Optional[str] = Field(None, description="Phone number")
    prompt: Optional[str] = Field(None, description="Inbound prompt")
    voice: Optional[str] = Field(None, description="Inbound voice")


class UpdateInboundTrunkRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: Optional[str] = Field(None, description="Trunk name")
    numbers: Optional[List[str]] = Field(None, description="List of phone numbers")
    allowed_addresses: Optional[List[str]] = Field(None, description="Allowed IP/CIDR addresses")
    allowed_numbers: Optional[List[str]] = Field(None, description="Allowed caller numbers")
    auth_username: Optional[str] = Field(None, description="SIP auth username")
    auth_password: Optional[str] = Field(None, description="SIP auth password")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Trunk metadata")


class UpdateDispatchRuleRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: Optional[str] = Field(None, description="Dispatch rule name")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Rule metadata")
    attributes: Optional[Dict[str, Any]] = Field(None, description="Rule attributes")
    trunk_ids: Optional[List[str]] = Field(None, description="List of trunk IDs")
    rule: Optional[Dict[str, Any]] = Field(None, description="Rule config")


class CreateZadarmaTrunkRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(..., description="Trunk name")
    address: str = Field(..., description="SIP server address")
    numbers: List[str] = Field(..., description="Caller numbers")
    auth_username: str = Field(..., description="SIP username")
    auth_password: str = Field(..., description="SIP password")


class CreateTwilioTrunkRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(..., description="Trunk name")
    numbers: List[str] = Field(..., description="Caller numbers")
    auth_username: str = Field(..., description="Twilio SIP auth username")
    auth_password: str = Field(..., description="Twilio SIP auth password")
    address: Optional[str] = Field(None, description="Twilio SIP termination URI")


class CreateVoiceLinkTrunkRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: Optional[str] = Field(None, description="VoiceLink trunk name")
    numbers: Optional[List[str]] = Field(None, description="VoiceLink caller numbers")
    auth_username: Optional[str] = Field(None, description="VoiceLink SIP username")
    auth_password: Optional[str] = Field(None, description="VoiceLink SIP password")
    trunk_id: Optional[str] = Field(None, description="Existing VoiceLink trunk ID")


class PlivoOutboundCallRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    client_phone: Optional[str] = Field(
        None,
        description="Target phone number to call in E.164 format",
    )
    client_country_code: Optional[str] = Field(
        None,
        description="Country code",
    )
    call_from: Optional[str] = Field(
        None,
        description="Caller ID number",
    )
    trunk_id: Optional[str] = Field(None, description="SIP trunk ID")
    org_id: Optional[Union[str, int]] = Field(None, description="Organization ID")
    call_id: Optional[Union[str, int]] = Field(None, description="Call ID")
    voice_id: Optional[Union[str, int]] = Field(None, description="Voice ID")
