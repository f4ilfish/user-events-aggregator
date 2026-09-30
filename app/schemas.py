from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Identifier(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    type: Literal["customer_id", "phone"]
    value: str = Field(min_length=1, max_length=256)


class CollectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identifiers: list[Identifier] = Field(min_length=1, max_length=100)


class ResolvedUser(BaseModel):
    user_id: str
    merged_ids: list[str]


class Profile(BaseModel):
    user_id: str
    tracking_id: int


class ProfilesResponse(BaseModel):
    profiles: list[Profile]


class UpstreamEvent(BaseModel):
    event_type: str
    event_time: datetime
    user_id: str
    session_id: int
    properties: dict[str, Any]

    @field_validator("event_time")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        # The mock contract defines naive timestamps as UTC.
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class EventsResponse(BaseModel):
    events: list[UpstreamEvent]


class Event(UpstreamEvent):
    tracking_id: int


class CollectionError(BaseModel):
    stage: Literal["resolve_user", "get_tracking_ids", "get_events"]
    id: str | int
    code: Literal["rate_limited", "upstream_error", "timeout", "deadline_exceeded", "invalid_response"]
    attempts: int


class UserResult(BaseModel):
    identifier: Identifier
    status: Literal["complete", "partial", "not_found", "failed"]
    user_ids: list[str] = Field(default_factory=list)
    tracking_ids: list[int] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    errors: list[CollectionError] = Field(default_factory=list)


class CollectResponse(BaseModel):
    results: list[UserResult]
