from __future__ import annotations

import base64
import email.utils
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


def _get_header(headers: list[dict[str, str]], name: str) -> str:
    return next(
        (h.get("value", "") for h in headers if h.get("name", "").lower() == name),
        "",
    )


def _extract_sender_domain(sender: str) -> str:
    email_address = email.utils.parseaddr(sender)[1]
    if "@" not in email_address:
        return ""
    return email_address.rsplit("@", 1)[1].lower()


def _decode_body_data(data: str | None) -> str:
    if not data:
        return ""

    try:
        decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (base64.binascii.Error, ValueError):
        return ""

    return decoded.decode("utf-8", errors="replace").strip()


def _extract_text_parts(payload: dict[str, Any]) -> list[str]:
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")
    parts = payload.get("parts", [])

    if mime_type == "text/plain":
        text = _decode_body_data(body_data)
        return [text] if text else []

    text_parts = []
    for part in parts:
        text_parts.extend(_extract_text_parts(part))
    return text_parts


def build_date_window_query(
    base_query: str = "in:inbox",
    after: str | None = None,
    before: str | None = None,
) -> str:
    query_parts = [part for part in [base_query.strip(), after, before] if part]
    if after:
        query_parts[-2 if before else -1] = f"after:{after}"
    if before:
        query_parts[-1] = f"before:{before}"
    return " ".join(query_parts).strip()


def search_emails_by_date_window(
    base_query: str = "in:inbox",
    after: str | None = None,
    before: str | None = None,
    max_results: int = 50,
):
    query = build_date_window_query(
        base_query=base_query,
        after=after,
        before=before,
    )
    return {
        "query": query,
        "messages": search_emails(query=query, max_results=max_results),
    }



def search_emails(query: str, max_results: int = 50):
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


def list_labels() -> list[dict[str, Any]]:
    service = get_service()
    result = _execute(service.users().labels().list(userId=_USER_ID))
    return result.get("labels", [])


def _find_label(label_name: str) -> dict[str, Any] | None:
    normalized = label_name.casefold()
    for label in list_labels():
        if label.get("id", "").casefold() == normalized:
            return label
        if label.get("name", "").casefold() == normalized:
            return label
    return None


def get_or_create_label(label_name: str, create_if_missing: bool = True) -> dict[str, Any]:
    label = _find_label(label_name)
    if label:
        return label

    if not create_if_missing:
        raise ValueError(f"Gmail label not found: {label_name}")

    service = get_service()
    return _execute(
        service.users()
        .labels()
        .create(
            userId=_USER_ID,
            body={
                "name": label_name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
    )


def apply_label_to_email(
    message_id: str,
    label_name: str,
    create_if_missing: bool = True,
) -> dict[str, Any]:
    email = read_email(message_id)
    label = get_or_create_label(
        label_name=label_name,
        create_if_missing=create_if_missing,
    )
    service = get_service()
    result = _execute(
        service.users()
        .messages()
        .modify(
            userId=_USER_ID,
            id=message_id,
            body={"addLabelIds": [label["id"]]},
        )
    )

    return {
        "id": result.get("id"),
        "thread_id": email.get("thread_id"),
        "subject": email.get("subject", ""),
        "from": email.get("from", ""),
        "label_name": label.get("name", label_name),
        "label_id": label.get("id"),
        "label_ids": result.get("labelIds", []),
    }


def search_emails_by_label(label_name: str, max_results: int = 10):
    label = get_or_create_label(label_name=label_name, create_if_missing=False)
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
                labelIds=[label["id"]],
                maxResults=page_size,
                pageToken=page_token,
                includeSpamTrash=False,
            )
        )
        messages.extend(result.get("messages", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return {
        "label_name": label.get("name", label_name),
        "label_id": label.get("id"),
        "messages": messages,
    }


def trash_emails_by_label(label_name: str, max_results: int = 10) -> dict[str, Any]:
    search_result = search_emails_by_label(
        label_name=label_name,
        max_results=max_results,
    )
    trashed = []
    for ref in search_result["messages"]:
        trashed.append(trash_email(ref["id"]))

    return {
        "label_name": search_result["label_name"],
        "label_id": search_result["label_id"],
        "requested_max": max_results,
        "trashed_count": len(trashed),
        "trashed": trashed,
    }



def read_email(message_id: str):
    service = get_service()
    msg = _execute(
        service.users()
        .messages()
        .get(userId=_USER_ID, id=message_id, format="full")
    )

    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    subject = _get_header(headers, "subject")
    sender = _get_header(headers, "from")
    sender_domain = _extract_sender_domain(sender)
    recipient = _get_header(headers, "to")
    date = _get_header(headers, "date")
    list_unsubscribe = _get_header(headers, "list-unsubscribe")
    snippet = msg.get("snippet", "")
    body_text = "\n\n".join(_extract_text_parts(payload))
    label_ids = msg.get("labelIds", [])

    return {
        "id": msg["id"],
        "thread_id": msg.get("threadId"),
        "date": date,
        "subject": subject,
        "from": sender,
        "sender_domain": sender_domain,
        "to": recipient,
        "snippet": snippet,
        "body_text": body_text,
        "label_ids": label_ids,
        "gmail_labels": label_ids,
        "list_unsubscribe_present": bool(list_unsubscribe),
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
