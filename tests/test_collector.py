import asyncio
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.collector import Collector, CollectorSettings
from app.main import create_app
from app.rate_limit import RateLimitSettings
from app.mocks import Failure, create_mock_services
from app.schemas import CollectRequest, EventsResponse, Identifier
from app.upstreams import RateLimitError


def request(*values):
    return CollectRequest(identifiers=[{"type": "customer_id", "value": v} for v in values])


class CollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_aliases_duplicates_and_distinct_people(self):
        services = create_mock_services(latency=0)
        body = request("C-100", "C-100", "C-200", "C-300", "missing")
        body.identifiers.append(Identifier(type="phone", value="+79990000001"))
        response = await Collector(services).collect(body)
        self.assertEqual([r.status for r in response.results],
                         ["complete", "complete", "complete", "complete", "not_found", "complete"])
        first = response.results[0]
        self.assertEqual(first.user_ids, ["u-1", "u-2"])
        self.assertEqual(len(first.events), 4)
        self.assertEqual(first.events, response.results[-1].events)
        self.assertEqual(first.events[0].user_id, "u-2")
        self.assertEqual(response.results[2].events, [])
        self.assertTrue(all(n == 1 for n in services.ids.calls.values()))
        self.assertTrue(all(n == 1 for n in services.profiles.calls.values()))
        self.assertTrue(all(n == 1 for n in services.events.calls.values()))

    async def test_transient_rate_limit_recovers(self):
        services = create_mock_services("rate_limit_then_ok", latency=0)
        response = await Collector(services).collect(request("C-100"))
        self.assertEqual(response.results[0].status, "complete")
        self.assertEqual(len(response.results[0].events), 4)
        self.assertEqual(services.events.calls[559012], 3)
        self.assertEqual(services.events.calls[128934], 1)

    async def test_persistent_rate_limit_and_upstream_failure(self):
        for scenario, code, attempts in [("rate_limit_always", "rate_limited", 3),
                                         ("upstream_error", "upstream_error", 1)]:
            services = create_mock_services(scenario, latency=0)
            response = await Collector(services).collect(request("C-100", "C-200"))
            result = response.results[0]
            self.assertEqual(result.status, "partial")
            self.assertEqual(len(result.events), 3)
            self.assertEqual(result.errors[0].model_dump(),
                             {"stage": "get_events", "id": 559012, "code": code, "attempts": attempts})
            self.assertEqual(response.results[1].status, "complete")

    async def test_resolution_failure_and_profile_failure(self):
        services = create_mock_services(latency=0)
        services.ids.failures[("customer_id", "C-200")] = Failure("upstream", times=None)
        services.profiles.failures["u-2"] = Failure("upstream", times=None)
        response = await Collector(services).collect(request("C-100", "C-200"))
        self.assertEqual(response.results[0].status, "partial")
        self.assertEqual(len(response.results[0].events), 3)
        self.assertEqual(response.results[0].errors[0].stage, "get_tracking_ids")
        self.assertEqual(response.results[1].status, "failed")
        self.assertNotIn("u-3", services.profiles.calls)

    async def test_call_timeout(self):
        services = create_mock_services(latency=0)
        services.events.latency = 0.1
        collector = Collector(services, CollectorSettings(call_timeout=0.01))
        result = (await collector.collect(request("C-100"))).results[0]
        self.assertEqual(result.status, "partial")
        self.assertEqual({e.code for e in result.errors}, {"timeout"})

    async def test_invalid_response_is_not_silent_empty_data(self):
        services = create_mock_services(latency=0)

        async def invalid(tid):
            return {"unexpected": []}

        services.events.get_events = invalid
        result = (await Collector(services).collect(request("C-100"))).results[0]
        self.assertEqual(result.status, "partial")
        self.assertEqual({e.code for e in result.errors}, {"invalid_response"})

    async def test_retry_without_retry_after_uses_growing_backoff_and_jitter(self):
        collector = Collector(create_mock_services(latency=0), CollectorSettings(backoff=0.5))
        loop = asyncio.get_running_loop()
        start = loop.time()
        now = start
        attempted_at = []

        async def upstream():
            attempted_at.append(now)
            if len(attempted_at) < 3:
                raise RateLimitError(retry_after=None)
            return {"events": []}

        async def advance(seconds):
            nonlocal now
            now += seconds

        # Only the clock, waiting, and randomness are controlled. The real retry
        # loop runs, and we check when the upstream is actually invoked.
        with patch.object(loop, "time", side_effect=lambda: now), \
             patch("app.collector.asyncio.sleep", side_effect=advance), \
             patch("app.collector.random.uniform", side_effect=[0.5, 1.5]):
            outcome = await collector._invoke(
                "get_events", 559012, upstream, EventsResponse, start + 30, "analytics"
            )
        self.assertIsNone(outcome.error)
        self.assertEqual(len(attempted_at), 3)
        # Base 0.5 s: first retry 0.25 s, second retry 1.5 s.
        self.assertAlmostEqual(attempted_at[1] - attempted_at[0], 0.25)
        self.assertAlmostEqual(attempted_at[2] - attempted_at[1], 1.5)

    async def test_retry_after_takes_precedence_over_backoff(self):
        collector = Collector(create_mock_services(latency=0), CollectorSettings(backoff=10))
        loop = asyncio.get_running_loop()
        start = loop.time()
        now = start
        attempted_at = []

        async def upstream():
            attempted_at.append(now)
            if len(attempted_at) == 1:
                raise RateLimitError(retry_after=0.75)
            return {"events": []}

        async def advance(seconds):
            nonlocal now
            now += seconds

        with patch.object(loop, "time", side_effect=lambda: now), \
             patch("app.collector.asyncio.sleep", side_effect=advance), \
             patch("app.collector.random.uniform") as jitter:
            outcome = await collector._invoke(
                "get_events", 559012, upstream, EventsResponse, start + 30, "analytics"
            )
        self.assertIsNone(outcome.error)
        self.assertEqual(len(attempted_at), 2)
        self.assertAlmostEqual(attempted_at[1] - attempted_at[0], 0.75)
        jitter.assert_not_called()

    async def test_each_event_preserves_tracking_source(self):
        result = (await Collector(create_mock_services(latency=0)).collect(request("C-100"))).results[0]
        self.assertEqual(result.status, "complete")
        # Two devices share u-1; a third belongs to merged account u-2.
        # Compare every event, not merely the set of tracking IDs.
        self.assertCountEqual(
            [(e.event_type, e.user_id, e.session_id, e.tracking_id) for e in result.events],
            [
                ("session_start", "u-1", 1782907200000, 128934),
                ("loan_application_submitted", "u-1", 1782907200000, 128934),
                ("session_start", "u-1", 1782982800000, 559012),
                ("session_start", "u-2", 1782806400000, 771003),
            ],
        )

    async def test_deadline_during_retry_keeps_successful_events(self):
        services = create_mock_services(latency=0)
        services.events.failures[559012] = Failure("rate_limit", times=None, retry_after=10)
        collector = Collector(services, CollectorSettings(request_timeout=0.05))
        response = await asyncio.wait_for(collector.collect(request("C-100")), timeout=0.5)
        result = response.results[0]
        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result.events), 3)
        self.assertEqual(result.errors[0].code, "deadline_exceeded")
        self.assertEqual(services.events.calls[559012], 1)

    async def test_concurrency_limit_and_cancellation_cleanup(self):
        services = create_mock_services(latency=0)
        original = services.events.get_events
        active = peak = 0

        async def tracked(tid):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.01)
                return await original(tid)
            finally:
                active -= 1

        services.events.get_events = tracked
        collector = Collector(services, CollectorSettings(concurrency=2))
        await asyncio.gather(*(collector.collect(request("C-100")) for _ in range(2)))
        self.assertEqual(peak, 2)
        task = asyncio.create_task(collector.collect(request("C-100")))
        await asyncio.sleep(0.005)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(active, 0)
        response = await collector.collect(request("C-100"))
        self.assertEqual(response.results[0].status, "complete")


class EndpointTests(unittest.TestCase):
    def test_post_and_validation(self):
        with TestClient(create_app(create_mock_services(latency=0),
                                   rate_limit=RateLimitSettings(requests=100))) as client:
            response = client.post("/users/collect", json=request("C-100").model_dump())
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.json()["results"][0]["events"]), 4)
            self.assertTrue(response.json()["results"][0]["events"][0]["event_time"].endswith("Z"))
            for body in ({"identifiers": []}, {"identifiers": [{"type": "email", "value": "a"}]},
                         {"identifiers": [{"type": "phone", "value": "  "}]}):
                self.assertEqual(client.post("/users/collect", json=body).status_code, 422)

    def test_partial_response_is_http_200(self):
        with TestClient(create_app(create_mock_services("upstream_error", latency=0))) as client:
            response = client.post("/users/collect", json=request("C-100").model_dump())
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["results"][0]["status"], "partial")
