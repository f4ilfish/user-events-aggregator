"""Three-stage fan-out with request-local deduplication and partial results."""

import asyncio
import random
from dataclasses import dataclass

from pydantic import ValidationError

from app.schemas import (
    CollectionError, CollectRequest, CollectResponse, Event, EventsResponse,
    ProfilesResponse, ResolvedUser, UserResult,
)
from app.upstreams import RateLimitError, Services, UpstreamError


@dataclass(frozen=True)
class CollectorSettings:
    concurrency: int = 8
    call_timeout: float = 1.0
    request_timeout: float = 5.0
    max_attempts: int = 3
    backoff: float = 0.05

    def __post_init__(self):
        if min(self.concurrency, self.call_timeout, self.request_timeout, self.max_attempts) <= 0:
            raise ValueError("Limits and timeouts must be positive")
        if self.backoff < 0:
            raise ValueError("backoff must be non-negative")


@dataclass
class Outcome:
    data: object = None
    error: CollectionError | None = None


class Collector:
    def __init__(self, services: Services, settings: CollectorSettings | None = None):
        self.services = services
        self.settings = settings or CollectorSettings()
        self._slots = asyncio.Semaphore(self.settings.concurrency)
        # Shared across requests in this process; profiles/events share analytics.
        self._cooldowns = {"ids": 0.0, "analytics": 0.0}

    async def _invoke(self, stage, key, call, model, deadline, scope):
        loop = asyncio.get_running_loop()
        attempts = 0

        def failure(code):
            return Outcome(error=CollectionError(stage=stage, id=key, code=code, attempts=attempts))

        try:
            async with asyncio.timeout_at(deadline):
                while attempts < self.settings.max_attempts:
                    if loop.time() >= deadline:
                        return failure("deadline_exceeded")
                    wait = self._cooldowns[scope] - loop.time()
                    if wait > 0:
                        await asyncio.sleep(wait)
                    async with self._slots:
                        # Another task may have set a cooldown while we queued.
                        if self._cooldowns[scope] > loop.time():
                            continue
                        attempts += 1
                        try:
                            async with asyncio.timeout(self.settings.call_timeout):
                                data = await call()
                            if data is None and stage == "resolve_user":
                                return Outcome()
                            return Outcome(data=model.model_validate(data))
                        except RateLimitError as exc:
                            delay = exc.retry_after
                            if delay is None:
                                delay = self.settings.backoff * 2 ** (attempts - 1) * random.uniform(0.5, 1.5)
                            self._cooldowns[scope] = max(self._cooldowns[scope], loop.time() + max(0, delay))
                            if attempts == self.settings.max_attempts:
                                return failure("rate_limited")
                        except UpstreamError:
                            return failure("upstream_error")
                        except TimeoutError:
                            return failure("timeout")
                        except ValidationError:
                            return failure("invalid_response")
        except TimeoutError:
            return failure("deadline_exceeded")

    async def collect(self, request: CollectRequest) -> CollectResponse:
        deadline = asyncio.get_running_loop().time() + self.settings.request_timeout
        identifiers = list(dict.fromkeys((i.type, i.value) for i in request.identifiers))
        resolved = dict(zip(identifiers, await asyncio.gather(*(
            self._invoke("resolve_user", value,
                         lambda kind=kind, value=value: self.services.ids.resolve_user(kind, value),
                         ResolvedUser, deadline, "ids")
            for kind, value in identifiers
        ))))
        user_ids = sorted({uid for result in resolved.values() if result.data is not None
                           for uid in [result.data.user_id, *result.data.merged_ids]})
        profiles = dict(zip(user_ids, await asyncio.gather(*(
            self._invoke("get_tracking_ids", uid,
                         lambda uid=uid: self.services.profiles.get_tracking_ids(uid),
                         ProfilesResponse, deadline, "analytics")
            for uid in user_ids
        ))))
        tracking_ids = sorted({p.tracking_id for result in profiles.values() if result.data is not None
                               for p in result.data.profiles})
        events = dict(zip(tracking_ids, await asyncio.gather(*(
            self._invoke("get_events", tid, lambda tid=tid: self.services.events.get_events(tid),
                         EventsResponse, deadline, "analytics")
            for tid in tracking_ids
        ))))

        results = []
        for identifier in request.identifiers:
            resolved_user = resolved[(identifier.type, identifier.value)]
            if resolved_user.error:
                results.append(UserResult(identifier=identifier, status="failed", errors=[resolved_user.error]))
                continue
            if resolved_user.data is None:
                results.append(UserResult(identifier=identifier, status="not_found"))
                continue
            user = resolved_user.data
            result = UserResult(identifier=identifier, status="complete",
                                user_ids=list(dict.fromkeys([user.user_id, *user.merged_ids])))
            tids = set()
            for uid in result.user_ids:
                outcome = profiles[uid]
                if outcome.error:
                    result.errors.append(outcome.error)
                else:
                    tids.update(p.tracking_id for p in outcome.data.profiles)
            result.tracking_ids = sorted(tids)
            for tid in result.tracking_ids:
                outcome = events[tid]
                if outcome.error:
                    result.errors.append(outcome.error)
                else:
                    result.events.extend(Event(**event.model_dump(), tracking_id=tid)
                                         for event in outcome.data.events)
            result.events.sort(key=lambda event: (event.event_time, event.tracking_id))
            if result.errors:
                result.status = "partial"
            results.append(result)
        return CollectResponse(results=results)
