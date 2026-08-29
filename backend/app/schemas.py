"""Pydantic request/response models shared across routers."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from .social_store import is_single_emoji
from .validation import UUID_PATTERN


class SearchFilters(BaseModel):
    query: Optional[str] = Field(None, max_length=500)
    personIds: Optional[List[str]] = None
    make: Optional[str] = Field(None, max_length=100)
    model: Optional[str] = Field(None, max_length=100)
    country: Optional[str] = Field(None, max_length=100)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    takenAfter: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}")
    takenBefore: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}")
    type: Optional[str] = Field(None, pattern=r"^(IMAGE|VIDEO|ALL)$")
    page: int = Field(1, ge=1, le=1000)
    size: int = Field(50, ge=1, le=100)

    @field_validator("personIds")
    @classmethod
    def validate_person_ids(cls, v):
        if v:
            for pid in v:
                if not UUID_PATTERN.match(pid):
                    raise ValueError(f"Invalid person ID: {pid}")
        return v


class PaginatedResponse(BaseModel):
    items: list
    total: int
    page: int
    size: int
    hasMore: bool


class IdentityPayload(BaseModel):
    displayName: Optional[str] = Field(None, max_length=64)
    personId: Optional[str] = None

    @field_validator("personId")
    @classmethod
    def validate_person_id(cls, v):
        if v is not None and not UUID_PATTERN.match(v):
            raise ValueError("Invalid person ID")
        return v


class ReactionPayload(IdentityPayload):
    emoji: str = Field(..., min_length=1, max_length=32)

    @field_validator("emoji")
    @classmethod
    def validate_emoji(cls, v):
        if not is_single_emoji(v):
            raise ValueError("Must be a single emoji from the system emoji keyboard")
        return v


class CommentPayload(IdentityPayload):
    body: str = Field(..., min_length=1, max_length=1000)
