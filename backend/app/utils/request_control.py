import json
import uuid
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from app.config import settings
from app.utils.redis_client import redis_client


def build_request_id() -> str:
    return uuid.uuid4().hex


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def check_rate_limit(key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
    current = redis_client.incr(key)
    if current == 1:
        redis_client.expire(key, window_seconds)
    ttl = redis_client.ttl(key)
    return current <= limit, max(ttl, 0)


def build_rate_limit_key(request: Request, scope: str) -> str:
    return f"rate-limit:{scope}:{get_client_ip(request)}"


def build_idempotency_key(request: Request, token: str, user_token: Optional[str]) -> str:
    identity = user_token or get_client_ip(request)
    return f"idempotency:{request.method}:{request.url.path}:{identity}:{token}"


def load_idempotent_response(key: str) -> Optional[JSONResponse]:
    cached = redis_client.get(key)
    if not cached:
        return None
    payload = json.loads(cached)
    response = JSONResponse(
        status_code=payload["status_code"],
        content=payload["body"],
    )
    response.headers["X-Idempotent-Replay"] = "true"
    return response


def should_store_idempotent_response(response: Response) -> bool:
    content_type = response.headers.get("content-type", "")
    return response.status_code < 500 and "application/json" in content_type


def store_idempotent_response(key: str, response: Response, body: object) -> None:
    payload = {
        "status_code": response.status_code,
        "body": body,
    }
    redis_client.setex(key, settings.IDEMPOTENCY_TTL_SECONDS, json.dumps(payload, ensure_ascii=False))
