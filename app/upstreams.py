"""Async contracts implemented by mocks and future HTTP clients."""

from dataclasses import dataclass
from typing import Protocol


class UpstreamError(Exception):
    pass


class RateLimitError(UpstreamError):
    def __init__(self, message: str = "Rate limit exceeded", *, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class IdService(Protocol):
    async def resolve_user(self, id_type: str, value: str) -> dict | None: ...


class ProfilesService(Protocol):
    async def get_tracking_ids(self, user_id: str) -> dict: ...


class EventsService(Protocol):
    async def get_events(self, tracking_id: int) -> dict: ...


@dataclass
class Services:
    ids: IdService
    profiles: ProfilesService
    events: EventsService
