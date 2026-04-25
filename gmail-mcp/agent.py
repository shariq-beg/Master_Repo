from __future__ import annotations

import asyncio
import argparse
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openpyxl import Workbook


OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3:8b"
OLLAMA_NUM_CTX = 8192
OLLAMA_NUM_PREDICT = 256
MCP_SERVER_SCRIPT = Path(__file__).with_name("server.py")
OUTPUT_FILE = Path(__file__).with_name("email_review_output.xlsx")
REVIEW_SESSIONS_DIR = Path(__file__).with_name("review_sessions")
REVIEW_DB_FILE = Path(__file__).with_name("gmail_review_db.json")

BASE_QUERY = "in:inbox category:primary"
AFTER_DATE = "2026/04/01"
BEFORE_DATE = "2026/04/25"
MAX_RESULTS = 50
BODY_CHAR_LIMIT = 12000
DEFAULT_INSTRUCTION = (
    f"Review emails matching {BASE_QUERY} from {AFTER_DATE} to {BEFORE_DATE}, "
    f"maximum {MAX_RESULTS} emails."
)

VALID_CLASSIFICATIONS = {
    "promotional_not_useful",
    "useful",
    "needs_further_review",
}
CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "classification": {
            "type": "string",
            "enum": [
                "promotional_not_useful",
                "useful",
                "needs_further_review",
            ],
        },
        "confidence": {
            "type": "string",
            "enum": ["low", "medium", "high"],
        },
        "reason": {
            "type": "string",
        },
    },
    "required": ["classification", "confidence", "reason"],
    "additionalProperties": False,
}


@dataclass
class ClassificationResult:
    classification: str
    confidence: str
    reason: str


@dataclass
class ReviewParameters:
    base_query: str
    after: str | None
    before: str | None
    max_results: int


def build_parameter_prompt(instruction: str) -> str:
    return f"""
/no_think

Extract safe Gmail review parameters from the user's instruction.

Return only valid JSON with this shape:
{{
  "base_query": "Gmail search query, default in:inbox category:primary",
  "after": "YYYY/MM/DD or null",
  "before": "YYYY/MM/DD or null",
  "max_results": 1-300
}}

Rules:
- Use Gmail date format YYYY/MM/DD.
- If the user does not provide a category or base query, use "in:inbox category:primary".
- If the user says scan Primary, use base_query "in:inbox category:primary".
- If the user says scan Promotions, Promotion, or Promotional, use base_query "in:inbox category:promotions".
- If the user says scan Social, Updates, or Forums, use the matching Gmail category query, for example "in:inbox category:social".
- If the user does not provide max results, use 50.
- Never exceed 300 max results.
- Do not include destructive actions such as delete, trash, archive, or label.

User instruction:
{instruction}
""".strip()


def parse_review_parameters(raw_response: str) -> ReviewParameters:
    start = raw_response.find("{")
    end = raw_response.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return ReviewParameters(BASE_QUERY, AFTER_DATE, BEFORE_DATE, MAX_RESULTS)

    try:
        parsed = json.loads(raw_response[start : end + 1])
    except json.JSONDecodeError:
        return ReviewParameters(BASE_QUERY, AFTER_DATE, BEFORE_DATE, MAX_RESULTS)

    base_query = str(parsed.get("base_query") or BASE_QUERY).strip() or BASE_QUERY
    after = parsed.get("after")
    before = parsed.get("before")

    if not isinstance(after, str):
        after = None
    if not isinstance(before, str):
        before = None

    try:
        max_results = int(parsed.get("max_results", MAX_RESULTS))
    except (TypeError, ValueError):
        max_results = MAX_RESULTS

    max_results = max(1, min(max_results, 300))
    return ReviewParameters(base_query, after, before, max_results)


def apply_category_focus(instruction: str, params: ReviewParameters) -> ReviewParameters:
    lowered = instruction.lower()
    category_query = None
    if "primary" in lowered:
        category_query = "in:inbox category:primary"
    elif any(word in lowered for word in ["promotions", "promotion", "promotional"]):
        category_query = "in:inbox category:promotions"
    elif "social" in lowered:
        category_query = "in:inbox category:social"
    elif "updates" in lowered:
        category_query = "in:inbox category:updates"
    elif "forums" in lowered:
        category_query = "in:inbox category:forums"

    if not category_query:
        return params
    return ReviewParameters(
        base_query=category_query,
        after=params.after,
        before=params.before,
        max_results=params.max_results,
    )


def parameters_from_instruction(instruction: str) -> ReviewParameters:
    prompt = build_parameter_prompt(instruction)
    raw_response = call_ollama(prompt)
    params = parse_review_parameters(raw_response)
    return apply_category_focus(instruction, params)


def build_classification_prompt(email: dict[str, Any]) -> str:
    body_text = (email.get("body_text") or "").strip()
    if len(body_text) > BODY_CHAR_LIMIT:
        body_text = body_text[:BODY_CHAR_LIMIT] + "\n[truncated]"

    return f"""
/no_think

Classify this Gmail message into exactly one category.

- promotional_not_useful: main purpose is marketing, offers, newsletters, promotions, product updates, digests, events, or bulk broadcast with no personal action required
- useful: clear personal relevance, such as financial, security, legal, account, purchase, booking, employment, job alerts, appointment, or action required
- needs_further_review: too little content to decide, sensitive or unusual content, or a mix of promotional and account-specific signals

Default toward promotional_not_useful for generic newsletters, recommendations, sales, and marketing.
For this user, LinkedIn job alerts and HR/People/Operations job alerts are useful.
Legal, policy, account, security, payment, or terms notices from services the user uses are useful or needs_further_review, even if no action is required.
Only use useful when there is clear personal importance or action relevance.
When uncertain, choose needs_further_review.

Examples:
From: LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>
Subject: Jobs you may be interested in
Snippet: New roles matching your profile
Classification: useful
Reason: Job alerts are relevant to this user's job search and career interests.

From: PayPal Communications <no_reply@communications.paypal.com>
Subject: We're making some changes to our PayPal legal agreements
Snippet: These changes will apply to you. No action is needed today.
Classification: useful
Reason: Account-specific legal agreement update from a service the user uses.

From: PayPal <service@paypal.com>
Subject: Confirm your account activity
Snippet: We noticed a login from a new device
Classification: useful
Reason: Security-related account alert.

From: ATO <no-reply@ato.gov.au>
Subject: New message in your myGov Inbox
Snippet: You have a new secure message
Classification: needs_further_review
Reason: Potentially important government message but the snippet lacks details.

From: Medium Daily Digest <noreply@medium.com>
Subject: Stories for you
Snippet: Recommended articles from writers you follow
Classification: promotional_not_useful
Reason: Generic content digest with no personal action required.

From: Airline <updates@example.com>
Subject: Your flight time has changed
Snippet: Your upcoming booking has been updated
Classification: useful
Reason: Account-specific travel booking update.

Email:
Date: {email.get("date", "")}
From: {email.get("from", "")}
Sender domain: {email.get("sender_domain", "")}
To: {email.get("to", "")}
Subject: {email.get("subject", "")}
Snippet: {email.get("snippet", "")}
Gmail labels: {email.get("gmail_labels", email.get("label_ids", []))}
List-Unsubscribe header present: {email.get("list_unsubscribe_present", False)}
Body:
{body_text}

Return only JSON. No markdown. No extra text.

/no_think
""".strip()


def call_ollama(prompt: str, response_format: dict[str, Any] | None = None) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0,
            "num_ctx": OLLAMA_NUM_CTX,
            "num_predict": OLLAMA_NUM_PREDICT,
        },
    }
    if response_format is not None:
        payload["format"] = response_format
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        OLLAMA_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Ollama at {OLLAMA_URL}: {exc}") from exc

    return response_payload.get("response", "")


def _strip_think_blocks(text: str) -> str:
    while "<think>" in text and "</think>" in text:
        start = text.find("<think>")
        end = text.find("</think>") + len("</think>")
        text = text[:start] + text[end:]
    return text.strip()


def compact_session_for_chat(session: dict[str, Any], max_items: int = 40) -> str:
    items = []
    for index, row in enumerate(session.get("items", [])[:max_items], 1):
        items.append(
            {
                "index": index,
                "message_id": row.get("message_id", ""),
                "classification": row.get("classification", ""),
                "confidence": row.get("confidence", ""),
                "from": row.get("from", ""),
                "subject": row.get("subject", ""),
                "reason": row.get("reason", ""),
                "snippet": row.get("snippet", ""),
            }
        )
    compact = {
        "session_id": session.get("session_id"),
        "summary": session.get("summary", {}),
        "parameters": session.get("parameters", {}),
        "items": items,
    }
    return json.dumps(compact, ensure_ascii=True)


def answer_session_question(session: dict[str, Any], question: str) -> str:
    prompt = f"""
/no_think

You are helping the user inspect a completed Gmail classification session.
Answer only from the session data below. If the answer is not in the data, say so.
Do not claim that you changed Gmail. Do not invent emails or labels.
Keep the answer concise and practical.

Session data:
{compact_session_for_chat(session)}

User question:
{question}

/no_think
""".strip()
    return _strip_think_blocks(call_ollama(prompt))


def parse_classification(raw_response: str) -> ClassificationResult:
    start = raw_response.find("{")
    end = raw_response.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return ClassificationResult(
            classification="needs_further_review",
            confidence="low",
            reason="Model did not return valid JSON.",
        )

    try:
        parsed = json.loads(raw_response[start : end + 1])
    except json.JSONDecodeError:
        return ClassificationResult(
            classification="needs_further_review",
            confidence="low",
            reason="Model returned malformed JSON.",
        )

    classification = parsed.get("classification", "needs_further_review")
    confidence = parsed.get("confidence", "low")
    reason = parsed.get("reason", "No reason provided.")

    if classification not in VALID_CLASSIFICATIONS:
        classification = "needs_further_review"

    if confidence not in {"low", "medium", "high"}:
        confidence = "low"

    return ClassificationResult(
        classification=classification,
        confidence=confidence,
        reason=str(reason).strip(),
    )


def classify_email(email: dict[str, Any]) -> ClassificationResult:
    prompt = build_classification_prompt(email)
    raw_response = call_ollama(prompt, response_format=CLASSIFICATION_SCHEMA)
    return parse_classification(raw_response)


def build_review_row(email: dict[str, Any], result: ClassificationResult) -> dict[str, str]:
    return normalize_review_row({
        "message_id": email.get("id", ""),
        "thread_id": email.get("thread_id", ""),
        "date": email.get("date", ""),
        "from": email.get("from", ""),
        "to": email.get("to", ""),
        "subject": email.get("subject", ""),
        "classification": result.classification,
        "confidence": result.confidence,
        "reason": result.reason,
        "snippet": email.get("snippet", ""),
        "sender_domain": email.get("sender_domain", ""),
        "gmail_labels": ", ".join(email.get("gmail_labels", email.get("label_ids", []))),
        "list_unsubscribe_present": str(email.get("list_unsubscribe_present", False)),
        "status": email.get("status", "reviewed"),
        "review_source": email.get("review_source", "new"),
        "labels_applied": ", ".join(email.get("labels_applied", [])),
    })


def _to_cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item is not None)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=True)
    return str(value)


def normalize_review_row(row: dict[str, Any]) -> dict[str, str]:
    normalized = dict(row)
    for key, value in list(normalized.items()):
        normalized[key] = _to_cell_text(value)
    normalized.setdefault("status", "reviewed")
    normalized.setdefault("review_source", "new")
    normalized.setdefault("labels_applied", "")
    return normalized


def load_review_db() -> dict[str, Any]:
    if not REVIEW_DB_FILE.exists():
        return {"messages": {}}

    try:
        db = json.loads(REVIEW_DB_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"messages": {}}

    messages = db.setdefault("messages", {})
    for message_id, row in list(messages.items()):
        messages[message_id] = normalize_review_row(row)
    return db


def save_review_db(db: dict[str, Any]) -> Path:
    db.setdefault("messages", {})
    db["updated_at"] = datetime.now().replace(microsecond=0).isoformat()
    REVIEW_DB_FILE.write_text(json.dumps(db, indent=2, ensure_ascii=True), encoding="utf-8")
    return REVIEW_DB_FILE


def get_cached_review(db: dict[str, Any], message_id: str) -> dict[str, Any] | None:
    row = db.get("messages", {}).get(message_id)
    if not row or row.get("status") == "deleted":
        return None
    cached = dict(row)
    cached["review_source"] = "cached"
    return normalize_review_row(cached)


def upsert_reviewed_message(db: dict[str, Any], row: dict[str, str]) -> None:
    row = normalize_review_row(row)
    message_id = row.get("message_id")
    if not message_id:
        return

    existing = db.setdefault("messages", {}).get(message_id, {})
    merged = {
        **existing,
        **row,
        "status": row.get("status") or existing.get("status") or "reviewed",
        "review_source": row.get("review_source", "new"),
        "last_seen_at": datetime.now().replace(microsecond=0).isoformat(),
    }
    if "reviewed_at" not in merged:
        merged["reviewed_at"] = merged["last_seen_at"]
    db["messages"][message_id] = merged


def update_message_status(
    db: dict[str, Any],
    message_id: str,
    status: str,
    label_name: str | None = None,
) -> None:
    row = db.setdefault("messages", {}).setdefault(message_id, {"message_id": message_id})
    row["status"] = status
    row["updated_at"] = datetime.now().replace(microsecond=0).isoformat()
    if status == "deleted":
        row["deleted_at"] = row["updated_at"]
        row["gmail_action"] = "trashed"
    if label_name:
        current_labels = row.get("labels_applied", [])
        if isinstance(current_labels, str):
            current_labels = current_labels.split(", ")
        labels = set(filter(None, current_labels))
        labels.add(label_name)
        row["labels_applied"] = ", ".join(sorted(labels))


def update_session_item_status(
    session: dict[str, Any],
    message_id: str,
    status: str,
    label_name: str | None = None,
) -> None:
    for row in session.get("items", []):
        if row.get("message_id") != message_id:
            continue
        row["status"] = status
        if label_name:
            current_labels = row.get("labels_applied", "")
            labels = set(filter(None, current_labels.split(", ")))
            labels.add(label_name)
            row["labels_applied"] = ", ".join(sorted(labels))
        break
    session["summary"] = build_summary(session.get("items", []))


def review_parameters_to_dict(params: ReviewParameters) -> dict[str, Any]:
    return {
        "base_query": params.base_query,
        "after": params.after,
        "before": params.before,
        "max_results": params.max_results,
    }


def build_summary(rows: list[dict[str, str]]) -> dict[str, int]:
    summary = {"total": len(rows)}
    for classification in sorted(VALID_CLASSIFICATIONS):
        summary[classification] = 0

    for row in rows:
        classification = row.get("classification", "needs_further_review")
        if classification not in summary:
            summary[classification] = 0
        summary[classification] += 1

    return summary


def create_session_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_review_session(
    instruction: str,
    params: ReviewParameters,
    rows: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "session_id": create_session_id(),
        "created_at": datetime.now().replace(microsecond=0).isoformat(),
        "instruction": instruction,
        "parameters": review_parameters_to_dict(params),
        "summary": build_summary(rows),
        "items": rows,
    }


def save_review_session(session: dict[str, Any]) -> Path:
    REVIEW_SESSIONS_DIR.mkdir(exist_ok=True)
    session_path = REVIEW_SESSIONS_DIR / f"{session['session_id']}.json"
    session_path.write_text(json.dumps(session, indent=2, ensure_ascii=True), encoding="utf-8")
    return session_path


def save_review_session_to_path(session: dict[str, Any], session_path: Path) -> Path:
    session["summary"] = build_summary(session.get("items", []))
    session["updated_at"] = datetime.now().replace(microsecond=0).isoformat()
    session_path.write_text(json.dumps(session, indent=2, ensure_ascii=True), encoding="utf-8")
    return session_path


def recategorize_session_item(
    session: dict[str, Any],
    item_index: int,
    new_classification: str,
    reason: str | None = None,
) -> dict[str, str]:
    if new_classification not in VALID_CLASSIFICATIONS:
        raise ValueError(f"Unsupported classification: {new_classification}")

    items = session.get("items", [])
    if item_index < 1 or item_index > len(items):
        raise IndexError(f"Email index {item_index} is outside the current session.")

    item = items[item_index - 1]
    old_classification = item.get("classification", "")
    item["classification"] = new_classification
    item["confidence"] = "manual"
    item["reason"] = reason or f"Manually recategorized from {old_classification}."
    session["summary"] = build_summary(items)
    return item


def top_review_candidates(rows: list[dict[str, str]], limit: int = 5) -> list[dict[str, str]]:
    needs_review = [
        row for row in rows if row.get("classification") == "needs_further_review"
    ]
    useful = [row for row in rows if row.get("classification") == "useful"]
    return (needs_review + useful)[:limit]


def format_chat_summary(session: dict[str, Any], session_path: Path, excel_path: Path) -> str:
    summary = session["summary"]
    params = session["parameters"]
    lines = [
        f"Review session: {session['session_id']}",
        "",
        f"Query: {params['base_query']}",
        f"Window: {params.get('after') or 'no after date'} to {params.get('before') or 'no before date'}",
        f"Reviewed: {summary['total']} emails",
        "",
        f"promotional_not_useful: {summary.get('promotional_not_useful', 0)}",
        f"useful: {summary.get('useful', 0)}",
        f"needs_further_review: {summary.get('needs_further_review', 0)}",
    ]

    candidates = top_review_candidates(session["items"])
    if candidates:
        lines.extend(["", "Top items to review:"])
        for index, row in enumerate(candidates, 1):
            subject = row.get("subject") or "(no subject)"
            sender = row.get("from") or "(unknown sender)"
            classification = row.get("classification", "")
            reason = row.get("reason", "")
            lines.append(f"{index}. [{classification}] {subject} - {sender}")
            lines.append(f"   Reason: {reason}")

    lines.extend(
        [
            "",
            f"Session JSON: {session_path}",
            f"Excel export: {excel_path}",
            "",
            "Next safe actions can use this session, for example: show needs_further_review, export useful, or prepare a label preview.",
        ]
    )
    return "\n".join(lines)


def write_excel(rows: list[dict[str, str]], output_file: Path = OUTPUT_FILE) -> Path:
    headers = [
        "message_id",
        "thread_id",
        "date",
        "from",
        "to",
        "subject",
        "classification",
        "confidence",
        "reason",
        "snippet",
        "sender_domain",
        "gmail_labels",
        "list_unsubscribe_present",
        "status",
        "review_source",
        "labels_applied",
    ]

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Email Review"
    worksheet.append(headers)

    for row in rows:
        normalized = normalize_review_row(row)
        worksheet.append([normalized.get(header, "") for header in headers])

    workbook.save(output_file)
    return output_file


def _extract_tool_payload(tool_result: Any) -> dict[str, Any]:
    structured_content = getattr(tool_result, "structuredContent", None)
    if isinstance(structured_content, dict):
        return structured_content

    content = getattr(tool_result, "content", [])
    if not content:
        return {}

    text = getattr(content[0], "text", "")
    if not text:
        return {}

    return json.loads(text)


async def call_gmail_tool(
    session: ClientSession,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    result = await session.call_tool(tool_name, arguments)
    return _extract_tool_payload(result)


async def run_gmail_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    server_params = StdioServerParameters(
        command="python",
        args=[str(MCP_SERVER_SCRIPT)],
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await call_gmail_tool(session, tool_name, arguments)


async def apply_label_with_mcp(
    message_id: str,
    label_name: str,
    create_if_missing: bool = True,
) -> dict[str, Any]:
    return await run_gmail_tool(
        "apply_gmail_label",
        {
            "message_id": message_id,
            "label_name": label_name,
            "create_if_missing": create_if_missing,
        },
    )


async def trash_messages_by_label_with_mcp(
    label_name: str,
    max_results: int,
) -> dict[str, Any]:
    return await run_gmail_tool(
        "trash_gmail_messages_by_label",
        {
            "label_name": label_name,
            "max_results": max_results,
        },
    )


ProgressCallback = Callable[[str, str], None]


def notify_progress(
    progress_callback: ProgressCallback | None,
    step: str,
    state: str,
) -> None:
    if progress_callback:
        progress_callback(step, state)


async def review_date_window(
    params: ReviewParameters,
    progress_callback: ProgressCallback | None = None,
) -> list[dict[str, str]]:
    review_db = load_review_db()
    server_params = StdioServerParameters(
        command="python",
        args=[str(MCP_SERVER_SCRIPT)],
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            notify_progress(progress_callback, "connect_mcp", "running")
            await session.initialize()
            notify_progress(progress_callback, "connect_mcp", "done")

            notify_progress(progress_callback, "search_gmail", "running")
            search_result = await call_gmail_tool(
                session,
                "search_gmail_date_window",
                {
                    "base_query": params.base_query,
                    "after": params.after,
                    "before": params.before,
                    "max_results": params.max_results,
                },
            )
            notify_progress(progress_callback, "search_gmail", "done")

            rows = []
            messages = search_result.get("messages", [])
            for index, message_ref in enumerate(messages, 1):
                notify_progress(
                    progress_callback,
                    "classify_emails",
                    f"running:{index}/{len(messages)}",
                )
                cached_row = get_cached_review(review_db, message_ref["id"])
                if cached_row:
                    rows.append(cached_row)
                    continue

                email = await call_gmail_tool(
                    session,
                    "get_gmail_message",
                    {"message_id": message_ref["id"]},
                )
                result = classify_email(email)
                row = build_review_row(email, result)
                rows.append(row)
                upsert_reviewed_message(review_db, row)
            save_review_db(review_db)
            notify_progress(progress_callback, "classify_emails", "done")
            return rows


async def run_review(
    instruction: str,
    progress_callback: ProgressCallback | None = None,
) -> tuple[dict[str, Any], Path, Path]:
    notify_progress(progress_callback, "parse_request", "running")
    params = parameters_from_instruction(instruction)
    notify_progress(progress_callback, "parse_request", "done")
    rows = await review_date_window(params, progress_callback=progress_callback)
    notify_progress(progress_callback, "save_outputs", "running")
    session = build_review_session(instruction, params, rows)
    session_path = save_review_session(session)
    output_file = write_excel(rows)
    notify_progress(progress_callback, "save_outputs", "done")
    return session, session_path, output_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Review Gmail messages through the Gmail MCP server using local Ollama."
    )
    parser.add_argument(
        "instruction",
        nargs="?",
        default=DEFAULT_INSTRUCTION,
        help="Natural-language review instruction.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    session, session_path, output_file = await run_review(args.instruction)
    print(format_chat_summary(session, session_path, output_file))


if __name__ == "__main__":
    asyncio.run(main())
