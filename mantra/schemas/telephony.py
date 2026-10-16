""" Telephony request schemas for Swagger documentation and validation. """

from typing import Any, Dict, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


class DispatchTestRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    phone_number: Optional[str] = Field(None, description="Target phone number in E.164 format")
    agent_name: Optional[str] = Field(None, description="Agent name")
    call_id: Optional[Union[str, int]] = Field(None, description="Call ID")
    prompt: Optional[str] = Field(None, description="Prompt text")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Custom metadata")
