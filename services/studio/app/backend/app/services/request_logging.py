import json
import logging
import threading
from collections import deque
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger("uvicorn.error")
_request_events: deque[dict[str, Any]] = deque(maxlen=500)
_request_events_lock = threading.Lock()
_next_event_id = 1
_active_request_event_id: ContextVar[int | None] = ContextVar("active_request_event_id", default=None)


def log_generation_request(endpoint: str, settings: Any, **extra: Any) -> dict[str, Any]:
    """Keep a bounded in-memory request feed and print it to the Uvicorn console."""
    global _next_event_id
    if hasattr(settings, "model_dump"):
        payload = settings.model_dump(mode="json")
    elif isinstance(settings, dict):
        payload = dict(settings)
    else:
        payload = {"value": settings}
    payload.update(extra)
    with _request_events_lock:
        event = {
            "id": _next_event_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "endpoint": endpoint,
            "payload": payload,
        }
        _next_event_id += 1
        _request_events.append(event)
        _active_request_event_id.set(event["id"])
    logger.info(
        "API 생성 요청 | %s | %s",
        endpoint,
        json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":")),
    )
    return event


def associate_generation_with_current_request(generation_id: int) -> None:
    """Attach the next created generation to the API request handled in this context."""
    event_id = _active_request_event_id.get()
    _active_request_event_id.set(None)
    if event_id is None:
        return
    with _request_events_lock:
        for event in _request_events:
            if event["id"] == event_id:
                event["generation_id"] = generation_id
                break


def list_generation_requests(limit: int = 100) -> list[dict[str, Any]]:
    with _request_events_lock:
        return [dict(event) for event in reversed(list(_request_events)[-limit:])]


def clear_generation_requests() -> int:
    with _request_events_lock:
        count = len(_request_events)
        _request_events.clear()
        return count
