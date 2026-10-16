""" Organization configuration request schemas for Swagger documentation and validation. """

from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class OrgConfigUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: Optional[str] = Field(None, description="Organization or assistant name")
    prompt: Optional[str] = Field(None, description="Agent system prompt")
    voice: Optional[str] = Field(None, description="Agent voice name")
    model: Optional[str] = Field(None, description="Agent model identifier")
    kb_tags: Optional[List[str]] = Field(None, description="Knowledge base tags")
    transfer_numbers: Optional[List[str]] = Field(None, description="Transfer numbers list")
    client_name: Optional[str] = Field(None, description="Client name")
    process_id: Optional[str] = Field(None, description="Process ID")
    is_active: Optional[bool] = Field(None, description="Whether active")
