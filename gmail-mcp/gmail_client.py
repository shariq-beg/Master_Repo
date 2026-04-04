from __future__ import annotations

import json
import random
import time
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from auth import get_credentials

_USER_ID = "me"
_PAGE_SIZE = 500
_MIN_REQUEST_INTERVAL_SECONDS = 0.05
_MAX_RETRIES = 5
_MAX_BACKOFF_SECONDS = 16.0
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_RETRYABLE_403_REASONS = {
    "backendError",
    "internalError",
    "rateLimitExceeded",
    "userRateLimitExceeded",
}
_SERVICE = None
_LAST_REQUEST_MONOTONIC = 0.0


def get_service():
    global _SERVICE
    if _SERVICE is None:
        creds = get_credentials()
        _SERVICE = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return _SERVICE



def _throttle() -> None:
    global _LAST_REQUEST_MONOTONIC

    elapsed = time.monotonic() - _LAST_REQUEST_MONOTONIC
    remaining = _MIN_REQUEST_INTERVAL_SECONDS - elapsed
    if remaining > 0:
        time.sleep(remaining)
    _LAST_REQUEST_MONOTONIC = time.monotonic()



def _extract_error_reason(exc: HttpError) -> str | None:
    content = getattr(exc, "content", None)
    if not content:
        return None

    try:
        payload = json.loads(content.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        return None

    error = payload.get("error", {})
    errors = error.get("errors", [])
    if errors and isinstance(errors[0], dict):
        reason = errors[0].get("reason")
        if isinstance(reason, str):
            return reason

    reason = error.get("status")
    return reason if isinstance(reason, str) else None



def _is_retryable_error(exc: HttpError) -> bool:
    status = getattr(exc.resp, "status", None)
    if status in _RETRYABLE_STATUSES:
        return True
    if status != 403:
        return False
    return _extract_error_reason(exc) in _RETRYABLE_403_REASONS



def _execute(request: Any) -> dict[str, Any]:
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            _throttle()
            return request.execute()
        except HttpError as exc:
            if not _is_retryable_error(exc) or attempt == _MAX_RETRIES:
                raise
            backoff_ceiling = min(_MAX_BACKOFF_SECONDS, 2**attempt)
            time.sleep(random.uniform(0.0, backoff_ceiling))

    raise RuntimeError("Gmail request retries exhausted")



def search_emails(query: str, max_results: int = 10):
    service = get_service()
    messages = []
    page_token = None

    while len(messages) < max_results:
        page_size = min(_PAGE_SIZE, max_results - len(messages))
        result = _execute(
            service.users()
            .messages()
            .list(
                userId=_USER_ID,
                q=query,
                maxResults=page_size,
                pageToken=page_token,
                includeSpamTrash=False,
            )
        )
        messages.extend(result.get("messages", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return messages



def read_email(message_id: str):
    service = get_service()
    msg = _execute(
        service.users()
        .messages()
        .get(userId=_USER_ID, id=message_id, format="full")
    )

    headers = msg.get("payload", {}).get("headers", [])
    subject = next((h["value"] for h in headers if h["name"].lower() == "subject"), "")
    sender = next((h["value"] for h in headers if h["name"].lower() == "from"), "")
    snippet = msg.get("snippet", "")

    return {
        "id": msg["id"],
        "thread_id": msg.get("threadId"),
        "subject": subject,
        "from": sender,
        "snippet": snippet,
        "label_ids": msg.get("labelIds", []),
    }



def preview_delete_candidates(query: str, max_results: int = 10):
    """Return lightweight previews for emails that matched a delete-candidate query.

    The MCP server should decide whether preview mode is enabled and call this helper
    only when the caller requested previews.
    """

    previews = []
    for ref in search_emails(query=query, max_results=max_results):
        email = read_email(ref["id"])
        previews.append(
            {
                "id": email["id"],
                "thread_id": email["thread_id"],
                "subject": email["subject"],
                "from": email["from"],
                "snippet": email["snippet"],
                "label_ids": email["label_ids"],
            }
        )
    return previews



def trash_email(message_id: str):
    email = read_email(message_id)
    service = get_service()
    result = _execute(
        service.users()
        .messages()
        .trash(userId=_USER_ID, id=message_id)
    )

    return {
        "id": result.get("id"),
        "thread_id": email.get("thread_id"),
        "subject": email.get("subject", ""),
        "from": email.get("from", ""),
        "label_ids": result.get("labelIds", []),
    }
