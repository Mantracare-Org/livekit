""" Knowledge Base request schemas for Swagger documentation and validation. """

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class KBChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    kb_ids: Optional[List[str]] = Field(None, description="List of KB IDs to query")
    kb_id: Optional[str] = Field(None, description="Single KB ID (backwards compatibility)")
    message: str = Field(..., description="User query text")
    history: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="Prior conversation history")


class KBTextRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    kb_id: str = Field(..., description="Target Knowledge Base collection ID")
    content: Optional[str] = Field(None, description="Text content to ingest")
    text: Optional[str] = Field(None, description="Alias for content")
    title: Optional[str] = Field(None, description="Document title")


class KBUrlRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    kb_id: str = Field(..., description="Target Knowledge Base collection ID")
    url: str = Field(..., description="Webpage URL to ingest and scrape")
