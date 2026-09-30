"""Async in-memory upstreams. Create a fresh bundle per demo/test."""

import asyncio
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal


from app.fixtures import create_fixtures
from app.upstreams import RateLimitError, UpstreamError


@dataclass(frozen=True)
class Failure:
    kind: Literal["rate_limit", "upstream"]
    # None means every call fails; otherwise fail the first N calls.
    times: int | None = 2
    retry_after: float | None = 0.05

    def __post_init__(self):
        if self.kind not in ("rate_limit", "upstream"):
            raise ValueError("Unsupported failure kind")
        if self.times is not None and self.times < 0:
            raise ValueError("times must be non-negative")
        if self.retry_after is not None and self.retry_after < 0:
            raise ValueError("retry_after must be non-negative")


class _MockService:
    def __init__(self, *, latency: float = 0.02, failures: dict | None = None):
        if latency < 0:
            raise ValueError("latency must be non-negative")
        self.latency = latency
        self.failures = dict(failures or {})
        self.calls = Counter()

    async def _before_call(self, key):
        self.calls[key] += 1
        attempt = self.calls[key]
        await asyncio.sleep(self.latency)
        failure = self.failures.get(key)
        if failure is None or (failure.times is not None and attempt > failure.times):
            return
        if failure.kind == "rate_limit":
            raise RateLimitError(retry_after=failure.retry_after)
        raise UpstreamError(f"Mock upstream failure for {key!r}")


class MockIdService(_MockService):
    def __init__(self, users: dict, **kwargs):
        super().__init__(**kwargs)
        self._users = deepcopy(users)

    async def resolve_user(self, id_type: str, value: str) -> dict | None:
        if id_type not in ("customer_id", "phone"):
            raise ValueError(f"Unsupported identifier type: {id_type}")
        key = (id_type, value)
        await self._before_call(key)
        return deepcopy(self._users.get(key))


class MockProfilesService(_MockService):
    def __init__(self, profiles: dict, **kwargs):
        super().__init__(**kwargs)
        self._profiles = deepcopy(profiles)

    async def get_tracking_ids(self, user_id: str) -> dict:
        await self._before_call(user_id)
        return {"profiles": deepcopy(self._profiles.get(user_id, []))}


class MockEventsService(_MockService):
    def __init__(self, events: dict, **kwargs):
        super().__init__(**kwargs)
        self._events = deepcopy(events)

    async def get_events(self, tracking_id: int) -> dict:
        await self._before_call(tracking_id)
        return {"events": deepcopy(self._events.get(tracking_id, []))}


@dataclass
class MockServices:
    ids: MockIdService
    profiles: MockProfilesService
    events: MockEventsService


def create_mock_services(
    scenario: Literal["happy", "rate_limit_then_ok", "rate_limit_always", "upstream_error"] = "happy",
    *,
    latency: float = 0.02,
) -> MockServices:
    """Failure scenarios target get_events(559012); other calls remain healthy."""
    failures = {
        "happy": {},
        "rate_limit_then_ok": {559012: Failure("rate_limit", times=2)},
        "rate_limit_always": {559012: Failure("rate_limit", times=None)},
        "upstream_error": {559012: Failure("upstream", times=None)},
    }
    if scenario not in failures:
        raise ValueError(f"Unknown scenario: {scenario}")

    users, profiles, events = create_fixtures()
    return MockServices(
        ids=MockIdService(users, latency=latency),
        profiles=MockProfilesService(profiles, latency=latency),
        events=MockEventsService(events, latency=latency, failures=failures[scenario]),
    )
