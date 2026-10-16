""" Authentication request schemas for Swagger documentation and validation. """

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., description="Administrator username")
    password: str = Field(..., description="Administrator password")
