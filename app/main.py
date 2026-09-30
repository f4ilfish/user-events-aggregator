import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from app.collector import Collector, CollectorSettings
from app.mocks import create_mock_services
from app.rate_limit import RateLimiter, RateLimitSettings
from app.schemas import CollectRequest, CollectResponse
from app.upstreams import Services


def get_collector(request: Request) -> Collector:
    return request.app.state.collector


def create_app(services: Services | None = None, settings: CollectorSettings | None = None,
               rate_limit: RateLimitSettings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.services = services if services is not None else create_mock_services(
            os.getenv("MOCK_SCENARIO", "happy")
        )
        app.state.collector = Collector(app.state.services, settings)
        app.state.rate_limiter = RateLimiter(rate_limit or RateLimitSettings())
        yield

    app = FastAPI(title="User collection mock MVP", lifespan=lifespan)

    @app.middleware("http")
    async def limit_requests(request: Request, call_next):
        if request.method != "POST" or request.url.path.rstrip("/") != "/users/collect":
            return await call_next(request)
        limiter = request.app.state.rate_limiter
        key = request.client.host if request.client else "unknown"
        retry_after, remaining = limiter.check(key)
        headers = {"X-RateLimit-Limit": str(limiter.settings.requests),
                   "X-RateLimit-Remaining": str(remaining)}
        if retry_after:
            return JSONResponse(
                status_code=429,
                content={"detail": {"code": "rate_limited", "retry_after": retry_after,
                                    "message": "Too many requests. Wait before retrying."}},
                headers={**headers, "Retry-After": str(retry_after)},
            )
        response = await call_next(request)
        response.headers.update(headers)
        return response

    @app.post("/users/collect", response_model=CollectResponse,
              description="Demo default: 3 requests per 15 seconds per IP. "
                          "Click Execute four times within 15 seconds to see HTTP 429. "
                          "Wait Retry-After seconds before retrying.",
              responses={429: {"description": "Incoming request rate limit exceeded",
                               "headers": {"Retry-After": {"schema": {"type": "integer"},
                                                           "description": "Seconds before retry"}},
                               "content": {"application/json": {"example": {
                                   "detail": {"code": "rate_limited", "retry_after": 15,
                                              "message": "Too many requests. Wait before retrying."}}}}}})
    async def collect_users(body: CollectRequest, collector: Collector = Depends(get_collector)):
        return await collector.collect(body)

    return app


app = create_app()
