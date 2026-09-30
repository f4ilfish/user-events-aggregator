import asyncio
import unittest

from app.mocks import Failure, MockProfilesService, create_mock_services
from app.upstreams import RateLimitError, UpstreamError


class MockTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_graph_and_alias(self):
        services = create_mock_services(latency=0)
        user = await services.ids.resolve_user("customer_id", "C-100")
        self.assertEqual(user, await services.ids.resolve_user("phone", "+79990000001"))
        profiles = await asyncio.gather(*(
            services.profiles.get_tracking_ids(uid)
            for uid in [user["user_id"], *user["merged_ids"]]
        ))
        ids = {p["tracking_id"] for result in profiles for p in result["profiles"]}
        self.assertEqual(ids, {128934, 559012, 771003})
        batches = await asyncio.gather(*(services.events.get_events(tid) for tid in ids))
        self.assertEqual(sum(len(batch["events"]) for batch in batches), 4)

    async def test_missing_and_empty(self):
        services = create_mock_services(latency=0)
        self.assertIsNone(await services.ids.resolve_user("phone", "missing"))
        self.assertEqual(await services.profiles.get_tracking_ids("u-4"), {"profiles": []})
        self.assertEqual(await services.events.get_events(880004), {"events": []})
        with self.assertRaises(ValueError):
            await services.ids.resolve_user("email", "someone@example.com")

    async def test_transient_limit_counts_concurrent_attempts(self):
        services = create_mock_services("rate_limit_then_ok", latency=0)
        results = await asyncio.gather(*(
            services.events.get_events(559012) for _ in range(3)
        ), return_exceptions=True)
        self.assertTrue(all(isinstance(r, RateLimitError) for r in results[:2]))
        self.assertEqual(results[0].retry_after, 0.05)
        self.assertEqual(len(results[2]["events"]), 1)
        self.assertEqual(services.events.calls[559012], 3)

    async def test_persistent_failures_leave_other_sources_available(self):
        for scenario, error in [("rate_limit_always", RateLimitError), ("upstream_error", UpstreamError)]:
            services = create_mock_services(scenario, latency=0)
            for _ in range(4):
                with self.assertRaises(error):
                    await services.events.get_events(559012)
            self.assertEqual(len((await services.events.get_events(128934))["events"]), 2)

    async def test_responses_and_instances_are_isolated(self):
        services = create_mock_services("rate_limit_then_ok", latency=0)
        response = await services.events.get_events(128934)
        response["events"][1]["properties"]["application_id"] = "changed"
        again = await services.events.get_events(128934)
        self.assertEqual(again["events"][1]["properties"]["application_id"], "APP-771")
        for _ in range(2):
            with self.assertRaises(RateLimitError):
                await services.events.get_events(559012)
        fresh = create_mock_services("rate_limit_then_ok", latency=0)
        with self.assertRaises(RateLimitError):
            await fresh.events.get_events(559012)

    async def test_profile_failure_and_timeout(self):
        profiles = MockProfilesService({}, latency=0, failures={"u-1": Failure("rate_limit", times=1)})
        with self.assertRaises(RateLimitError):
            await profiles.get_tracking_ids("u-1")
        self.assertEqual(await profiles.get_tracking_ids("u-1"), {"profiles": []})
        slow = create_mock_services(latency=1)
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(slow.events.get_events(128934), timeout=0.01)


if __name__ == "__main__":
    unittest.main()
