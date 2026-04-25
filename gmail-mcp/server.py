from mcp.server.fastmcp import FastMCP

from gmail_client import (
    search_emails,
    search_emails_by_date_window,
    read_email,
    apply_label_to_email,
    preview_delete_candidates,
    trash_emails_by_label,
    trash_email,
)

mcp = FastMCP("gmail-mcp")


@mcp.tool()
def search_gmail(query: str, max_results: int = 50) -> dict:
    """
    Search Gmail using Gmail query syntax.

    Args:
        query: Gmail search query, for example 'category:promotions older_than:30d'
        max_results: Maximum number of matching message references to return
    """
    results = search_emails(query=query, max_results=max_results)
    return {
        "query": query,
        "count": len(results),
        "messages": results,
    }


@mcp.tool()
def search_gmail_date_window(
    base_query: str = "in:inbox",
    after: str | None = None,
    before: str | None = None,
    max_results: int = 50,
) -> dict:
    """
    Search Gmail within an optional date window.

    Args:
        base_query: Gmail search query to combine with dates, for example 'in:inbox'
        after: Optional Gmail date in YYYY/MM/DD format, for example '2026/04/01'
        before: Optional Gmail date in YYYY/MM/DD format, for example '2026/04/25'
        max_results: Maximum number of matching message references to return
    """
    result = search_emails_by_date_window(
        base_query=base_query,
        after=after,
        before=before,
        max_results=max_results,
    )
    return {
        "query": result["query"],
        "count": len(result["messages"]),
        "messages": result["messages"],
    }


@mcp.tool()
def get_gmail_message(message_id: str) -> dict:
    """
    Read a Gmail message by its message ID.

    Args:
        message_id: Gmail message ID returned by search_gmail
    """
    return read_email(message_id)


@mcp.tool()
def apply_gmail_label(
    message_id: str,
    label_name: str,
    create_if_missing: bool = True,
) -> dict:
    """
    Apply a Gmail label to one message.

    Args:
        message_id: Gmail message ID to label
        label_name: Gmail label name to apply
        create_if_missing: Whether to create the label if it does not exist
    """
    return apply_label_to_email(
        message_id=message_id,
        label_name=label_name,
        create_if_missing=create_if_missing,
    )


@mcp.tool()
def trash_gmail_messages_by_label(label_name: str, max_results: int = 10) -> dict:
    """
    Move up to max_results Gmail messages with a label to Trash.

    This is a destructive action in the Gmail sense: messages are moved to Trash,
    not permanently deleted.

    Args:
        label_name: Gmail label name or label ID to search
        max_results: Maximum number of labeled messages to move to Trash
    """
    return trash_emails_by_label(label_name=label_name, max_results=max_results)


@mcp.tool()
def preview_delete(query: str, max_results: int = 10) -> dict:
    """
    Preview which emails match a delete query before any delete action is taken.

    Args:
        query: Gmail search query used to find delete candidates
        max_results: Maximum number of preview candidates to return
    """
    previews = preview_delete_candidates(query=query, max_results=max_results)
    return {
        "query": query,
        "count": len(previews),
        "candidates": previews,
    }


@mcp.tool()
def move_gmail_message_to_trash(message_id: str) -> dict:
    """
    Move a Gmail message to Trash.

    Args:
        message_id: Gmail message ID to move to Trash
    """
    return trash_email(message_id)


if __name__ == "__main__":
    mcp.run()
