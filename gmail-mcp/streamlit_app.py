from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from agent import (
    DEFAULT_INSTRUCTION,
    answer_session_question,
    apply_label_with_mcp,
    build_summary,
    format_chat_summary,
    load_review_db,
    recategorize_session_item,
    run_review,
    save_review_db,
    save_review_session_to_path,
    update_message_status,
    update_session_item_status,
    write_excel,
    trash_messages_by_label_with_mcp,
)


WELCOME_MESSAGE = "Tell me what Gmail window to review, for example: Review inbox emails from 2026/04/20 to 2026/04/25, max 5."
PROGRESS_STEPS = [
    ("parse_request", "Parse request"),
    ("connect_mcp", "Connect Gmail MCP"),
    ("search_gmail", "Search Gmail"),
    ("classify_emails", "Read and classify emails"),
    ("save_outputs", "Save session and Excel"),
]


st.set_page_config(
    page_title="Gmail Review Agent",
    page_icon="",
    layout="wide",
)


def ensure_state() -> None:
    if "messages" not in st.session_state:
        reset_chat_state()
    if "latest_session" not in st.session_state:
        st.session_state.latest_session = None
    if "latest_session_path" not in st.session_state:
        st.session_state.latest_session_path = None
    if "latest_excel_path" not in st.session_state:
        st.session_state.latest_excel_path = None
    if "pending_action" not in st.session_state:
        st.session_state.pending_action = None


def reset_chat_state() -> None:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": WELCOME_MESSAGE,
        }
    ]
    st.session_state.latest_session = None
    st.session_state.latest_session_path = None
    st.session_state.latest_excel_path = None
    st.session_state.pending_action = None


def render_progress(progress_state: dict[str, str], container) -> None:
    lines = []
    for key, label in PROGRESS_STEPS:
        state = progress_state.get(key, "pending")
        if state == "done":
            marker = "[x]"
        elif state.startswith("running"):
            marker = "[...]"
        else:
            marker = "[ ]"

        detail = ""
        if state.startswith("running:"):
            detail = f" ({state.split(':', 1)[1]})"
        lines.append(f"{marker} {label}{detail}")
    container.markdown("```text\n" + "\n".join(lines) + "\n```")


def run_review_sync(instruction: str, progress_container):
    progress_state = {key: "pending" for key, _ in PROGRESS_STEPS}
    render_progress(progress_state, progress_container)

    def update_progress(step: str, state: str) -> None:
        progress_state[step] = state
        render_progress(progress_state, progress_container)

    return asyncio.run(run_review(instruction, progress_callback=update_progress))


def is_review_request(prompt: str) -> bool:
    lowered = prompt.lower()
    return any(
        phrase in lowered
        for phrase in [
            "review ",
            "scan ",
            "classify ",
            "check inbox",
            "check primary",
            "check promotion",
            "check promotions",
        ]
    )


def rows_for_category(session: dict, category: str) -> list[dict]:
    rows = session.get("items", [])
    if category == "all":
        return rows
    return [row for row in rows if row.get("classification") == category]


def normalize_category(value: str) -> str | None:
    normalized = value.lower().strip().replace(" ", "_")
    aliases = {
        "promotional": "promotional_not_useful",
        "promotions": "promotional_not_useful",
        "promo": "promotional_not_useful",
        "not_useful": "promotional_not_useful",
        "needs_review": "needs_further_review",
        "further_review": "needs_further_review",
        "review": "needs_further_review",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in {
        "promotional_not_useful",
        "useful",
        "needs_further_review",
    }:
        return normalized
    return None


def parse_recategorize_request(prompt: str, session: dict) -> dict | None:
    lowered = prompt.lower()
    if not any(word in lowered for word in ["recategorise", "recategorize", "mark", "change"]):
        return None

    category_match = re.search(
        r"\b(?:as|to)\s+(promotional_not_useful|promotional|promotions|promo|not useful|useful|needs_further_review|needs review|review)\b",
        lowered,
    )
    if not category_match:
        return None

    category = normalize_category(category_match.group(1))
    if not category:
        return None

    index_match = re.search(r"\b(?:email|row|item)\s+(\d+)\b", lowered)
    if index_match:
        return {
            "type": "recategorize",
            "item_index": int(index_match.group(1)),
            "classification": category,
        }

    target_text = prompt[: category_match.start()].strip()
    target_text = re.sub(
        r"(?i)\b(recategorise|recategorize|mark|change|the|email|message)\b",
        "",
        target_text,
    ).strip()
    if not target_text:
        return None

    matches = []
    for index, row in enumerate(session.get("items", []), 1):
        haystack = f"{row.get('subject', '')} {row.get('from', '')}".lower()
        if target_text.lower() in haystack:
            matches.append((index, row))

    if len(matches) == 1:
        return {
            "type": "recategorize",
            "item_index": matches[0][0],
            "classification": category,
        }
    if len(matches) > 1:
        candidates = "\n".join(
            f"{index}. {row.get('subject', '(no subject)')} - {row.get('from', '')}"
            for index, row in matches[:10]
        )
        return {
            "type": "ambiguous_recategorize",
            "message": f"I found multiple matches. Please recategorise by row number:\n{candidates}",
        }
    return None


def execute_recategorize_action(action: dict) -> str:
    session = st.session_state.latest_session
    session_path = st.session_state.latest_session_path
    excel_path = st.session_state.latest_excel_path

    item = recategorize_session_item(
        session=session,
        item_index=action["item_index"],
        new_classification=action["classification"],
    )
    review_db = load_review_db()
    message_id = item.get("message_id")
    if message_id:
        review_db.setdefault("messages", {}).setdefault(message_id, {}).update(item)
        update_message_status(review_db, message_id, status="manually_recategorised")
        save_review_db(review_db)
    save_review_session_to_path(session, session_path)
    write_excel(session.get("items", []), excel_path)

    subject = item.get("subject") or "(no subject)"
    return (
        f"Updated email {action['item_index']} to `{action['classification']}`.\n\n"
        f"Subject: {subject}"
    )


def parse_label_request(prompt: str, session: dict) -> dict | None:
    lowered = prompt.lower()
    if not any(word in lowered for word in ["label", "labelled", "labeled"]):
        return None

    category_match = re.search(
        r"\b(promotional_not_useful|promotional|promotions|useful|needs_further_review|needs review|all)\b",
        lowered,
    )
    if not category_match:
        return None

    explicit_label_match = re.search(
        r"(?:as|with label|label name)\s+['\"]?([^'\"]+?)['\"]?$",
        prompt,
        re.IGNORECASE,
    )

    category = category_match.group(1).replace(" ", "_")
    if category in {"promotional", "promotions"}:
        category = "promotional_not_useful"

    label_name = explicit_label_match.group(1).strip() if explicit_label_match else category
    rows = rows_for_category(session, category)
    return {
        "type": "label",
        "category": category,
        "label_name": label_name,
        "items": rows,
        "message_ids": [row["message_id"] for row in rows if row.get("message_id")],
        "count": len(rows),
    }


def parse_trash_request(prompt: str) -> dict | None:
    lowered = prompt.lower()
    if "trash" not in lowered:
        return None

    label_match = re.search(
        r"(?:label(?:ed)?|with label)\s+['\"]?([^'\"]+?)['\"]?(?:\s|$)",
        prompt,
        re.IGNORECASE,
    )
    if not label_match:
        return None

    count_match = re.search(r"\b(?:first|max|limit)?\s*(\d+)\b", prompt)
    max_results = int(count_match.group(1)) if count_match else 10
    return {
        "type": "trash_by_label",
        "label_name": label_match.group(1).strip(),
        "max_results": max(1, min(max_results, 300)),
    }


def describe_pending_action(action: dict) -> str:
    if action["type"] == "label":
        email_lines = "\n".join(
            f"{index}. {row.get('subject', '(no subject)')} - {row.get('from', '')}"
            for index, row in enumerate(action.get("items", [])[:20], 1)
        )
        if action.get("count", 0) > 20:
            email_lines += f"\n...and {action['count'] - 20} more."

        return (
            f"Preview: apply Gmail label `{action['label_name']}` to "
            f"{action['count']} `{action['category']}` emails from the latest session.\n\n"
            f"Emails to label:\n{email_lines or '(none)'}\n\n"
            "Reply `confirm` to apply the label, or `cancel` to stop."
        )
    if action["type"] == "trash_by_label":
        return (
            f"Preview: move up to {action['max_results']} Gmail messages with label "
            f"`{action['label_name']}` to Trash.\n\n"
            "Reply `confirm` to move them to Trash, or `cancel` to stop."
        )
    return "Unknown pending action."


def execute_pending_action_sync(action: dict) -> str:
    if action["type"] == "label":
        results = []
        review_db = load_review_db()
        for message_id in action["message_ids"]:
            results.append(
                asyncio.run(
                    apply_label_with_mcp(
                        message_id=message_id,
                        label_name=action["label_name"],
                    )
                )
            )
            update_message_status(
                review_db,
                message_id=message_id,
                status="labelled_to_be_deleted"
                if action["label_name"].lower() == "to be deleted"
                else "labelled",
                label_name=action["label_name"],
            )
            update_session_item_status(
                st.session_state.latest_session,
                message_id=message_id,
                status="labelled_to_be_deleted"
                if action["label_name"].lower() == "to be deleted"
                else "labelled",
                label_name=action["label_name"],
            )
        save_review_db(review_db)
        save_review_session_to_path(
            st.session_state.latest_session,
            st.session_state.latest_session_path,
        )
        write_excel(
            st.session_state.latest_session.get("items", []),
            st.session_state.latest_excel_path,
        )
        return f"Applied label `{action['label_name']}` to {len(results)} emails."

    if action["type"] == "trash_by_label":
        result = asyncio.run(
            trash_messages_by_label_with_mcp(
                label_name=action["label_name"],
                max_results=action["max_results"],
            )
        )
        review_db = load_review_db()
        trashed_ids = [row.get("id") for row in result.get("trashed", []) if row.get("id")]
        for message_id in trashed_ids:
            update_message_status(review_db, message_id=message_id, status="deleted")

        if st.session_state.latest_session:
            st.session_state.latest_session["items"] = [
                row
                for row in st.session_state.latest_session.get("items", [])
                if row.get("message_id") not in set(trashed_ids)
            ]
            st.session_state.latest_session["summary"] = build_summary(
                st.session_state.latest_session["items"]
            )
            save_review_session_to_path(
                st.session_state.latest_session,
                st.session_state.latest_session_path,
            )
            write_excel(
                st.session_state.latest_session.get("items", []),
                st.session_state.latest_excel_path,
            )
        save_review_db(review_db)
        return (
            f"Moved {result.get('trashed_count', 0)} emails with label "
            f"`{result.get('label_name', action['label_name'])}` to Trash."
        )

    return "I could not execute that action."


def handle_followup(prompt: str) -> str:
    lowered = prompt.lower().strip()
    pending_action = st.session_state.pending_action
    if pending_action:
        if lowered in {"confirm", "yes", "proceed", "do it"}:
            result = execute_pending_action_sync(pending_action)
            st.session_state.pending_action = None
            return result
        if lowered in {"cancel", "stop", "no"}:
            st.session_state.pending_action = None
            return "Cancelled the pending action."
        return "There is a pending action. Reply `confirm` to proceed or `cancel` to stop."

    if lowered in {"confirm", "yes", "proceed", "do it"}:
        return "There is no pending action to confirm. Ask me to label or trash something first, and I will show a preview."

    session = st.session_state.latest_session
    recategorize_action = parse_recategorize_request(prompt, session)
    if recategorize_action:
        if recategorize_action["type"] == "ambiguous_recategorize":
            return recategorize_action["message"]
        return execute_recategorize_action(recategorize_action)

    label_action = parse_label_request(prompt, session)
    if label_action:
        st.session_state.pending_action = label_action
        return describe_pending_action(label_action)

    trash_action = parse_trash_request(prompt)
    if trash_action:
        st.session_state.pending_action = trash_action
        return describe_pending_action(trash_action)

    return answer_session_question(session, prompt)


def render_metrics(session: dict) -> None:
    summary = session["summary"]
    cols = st.columns(4)
    cols[0].metric("Reviewed", summary.get("total", 0))
    cols[1].metric("Promotional", summary.get("promotional_not_useful", 0))
    cols[2].metric("Useful", summary.get("useful", 0))
    cols[3].metric("Needs Review", summary.get("needs_further_review", 0))


def render_review_table(session: dict) -> None:
    rows = session.get("items", [])
    if not rows:
        st.info("No reviewed emails in this session.")
        return

    df = pd.DataFrame(rows)
    category = st.selectbox(
        "Filter",
        ["all", "promotional_not_useful", "useful", "needs_further_review"],
        index=0,
    )
    if category != "all":
        df = df[df["classification"] == category]

    visible_columns = [
        "classification",
        "confidence",
        "status",
        "review_source",
        "labels_applied",
        "subject",
        "from",
        "sender_domain",
        "reason",
        "date",
        "gmail_labels",
        "list_unsubscribe_present",
        "snippet",
        "message_id",
    ]
    st.dataframe(
        df[[column for column in visible_columns if column in df.columns]],
        use_container_width=True,
        hide_index=True,
    )


def render_downloads(session_path: Path | None, excel_path: Path | None) -> None:
    cols = st.columns(2)
    if session_path and session_path.exists():
        cols[0].download_button(
            "Download Session JSON",
            data=session_path.read_bytes(),
            file_name=session_path.name,
            mime="application/json",
        )
    if excel_path and excel_path.exists():
        cols[1].download_button(
            "Download Excel",
            data=excel_path.read_bytes(),
            file_name=excel_path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


ensure_state()

st.title("Gmail Review Agent")

with st.sidebar:
    st.subheader("Current Run")
    st.caption("Gmail is read through the MCP server. This app does not label, archive, delete, or move emails.")
    if st.button("Use Example Prompt"):
        st.session_state.example_prompt = DEFAULT_INSTRUCTION
    if st.button("Clear Chat"):
        reset_chat_state()
        st.rerun()

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.write(message["content"])

prompt = st.chat_input(
    "Review inbox emails from 2026/04/20 to 2026/04/25, max 5"
)
if "example_prompt" in st.session_state:
    prompt = st.session_state.pop("example_prompt")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.write(prompt)

    should_run_review = st.session_state.latest_session is None or is_review_request(prompt)

    with st.chat_message("assistant"):
        if should_run_review:
            with st.status("Running Gmail review...", expanded=True) as status:
                progress_container = st.empty()
                session, session_path, excel_path = run_review_sync(
                    prompt,
                    progress_container,
                )
                status.update(label="Review complete", state="complete")

            summary_text = format_chat_summary(session, session_path, excel_path)
            st.session_state.latest_session = session
            st.session_state.latest_session_path = session_path
            st.session_state.latest_excel_path = excel_path
        else:
            with st.status("Thinking...", expanded=False) as status:
                summary_text = handle_followup(prompt)
                status.update(label="Done", state="complete")

        st.write(summary_text)

    st.session_state.messages.append({"role": "assistant", "content": summary_text})
    st.rerun()

if st.session_state.latest_session:
    st.divider()
    st.subheader("Latest Review")
    render_metrics(st.session_state.latest_session)
    render_review_table(st.session_state.latest_session)
    render_downloads(
        st.session_state.latest_session_path,
        st.session_state.latest_excel_path,
    )
