from mcp.server.fastmcp import FastMCP

from gmail_client import (
    search_emails,
    read_email,
    preview_delete_candidates,
    trash_email,
)

mcp = FastMCP("gmail-mcp")


@mcp.tool()
def search_gmail(query: str, max_results: int = 10) -> dict:
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
def get_gmail_message(message_id: str) -> dict:
    """
    Read a Gmail message by its message ID.

    Args:
        message_id: Gmail message ID returned by search_gmail
    """
    return read_email(message_id)


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