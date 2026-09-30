import unittest

from fastapi.testclient import TestClient

from app.main import create_app
from app.mocks import create_mock_services
from app.rate_limit import RateLimiter, RateLimitSettings


class RateLimitTests(unittest.TestCase):
    def test_sliding_window_and_independent_clients(self):
        now = 0.0
        limiter = RateLimiter(RateLimitSettings(), clock=lambda: now)
        self.assertEqual(limiter.check("a"), (0, 2))
        now = 1.0
        limiter.check("a")
        now = 2.0
        limiter.check("a")
        self.assertEqual(limiter.check("a"), (13, 0))
        self.assertEqual(limiter.check("b"), (0, 2))
        now = 14.5
        self.assertEqual(limiter.check("a"), (1, 0))
        now = 15.0
        self.assertEqual(limiter.check("a"), (0, 0))
        self.assertEqual(limiter.check("a"), (1, 0))

    def test_http_block_recovery_and_docs(self):
        services = create_mock_services(latency=0)
        app = create_app(services)
        now = 0.0
        body = {"identifiers": [{"type": "customer_id", "value": "C-100"}]}
        with TestClient(app) as client:
            app.state.rate_limiter.clock = lambda: now
            for remaining in (2, 1, 0):
                response = client.post("/users/collect", json=body)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["X-RateLimit-Remaining"], str(remaining))
            calls = dict(services.ids.calls)
            for _ in range(2):
                response = client.post("/users/collect", json=body)
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response.headers["Retry-After"], "15")
                self.assertEqual(response.json()["detail"]["code"], "rate_limited")
            self.assertEqual(dict(services.ids.calls), calls)
            self.assertEqual(client.get("/docs").status_code, 200)
            self.assertEqual(client.get("/openapi.json").status_code, 200)
            now = 15.0
            self.assertEqual(client.post("/users/collect", json=body).status_code, 200)

    def test_invalid_requests_also_consume_quota(self):
        with TestClient(create_app(create_mock_services(latency=0))) as client:
            for _ in range(3):
                self.assertEqual(client.post("/users/collect", json={}).status_code, 422)
            self.assertEqual(client.post("/users/collect", json={}).status_code, 429)
