"""Probe Qwen thinking controls through an OpenAI-compatible local endpoint.

This utility is intentionally standalone. It does not import the Gmail MCP app,
load Gmail tools, read credentials, or write result files.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from typing import Any

from openai import OpenAI


DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_API_KEY = "lm-studio"
DEFAULT_MODEL = "qwen/qwen3.5-9b"
DEFAULT_MAX_TOKENS = 700
DEFAULT_TIMEOUT_SECONDS = 120.0

DEFAULT_USER_PROMPT = "what is the best way to explore Sydney in one day?"
TOURIST_GUIDE_SYSTEM_PROMPT = (
    "You are a tourist guide and trip planning expert who helps users create "
    "itineraries for exploring famous cities of the world."
)


@dataclass(frozen=True)
class PromptHandleCase:
    """Store one prompt-handle test case.
    Args:
        name: Human-readable case name printed in probe output.
        handle: Thinking handle to place in either the system or user prompt.
        placement: Prompt role where the handle should be placed.
        base_user_prompt: User prompt text to send after any prompt handle.
    Used by:
        PROMPT_HANDLE_CASES, print_case_header, and run_probe_case.
    """

    name: str
    handle: str
    placement: str
    base_user_prompt: str

    def system_prompt(self) -> str:
        """Build the system prompt for this prompt-handle case.
        Args:
            None.
        Returns:
            The tourist-guide system prompt, with the handle prepended for system cases.
        Raises:
            None.
        Side effects:
            None.
        Used by:
            run_probe_case when constructing chat completion messages.
        """
        if self.placement == "system":
            return f"{self.handle}\n{TOURIST_GUIDE_SYSTEM_PROMPT}"
        return TOURIST_GUIDE_SYSTEM_PROMPT

    def user_prompt(self) -> str:
        """Build the user prompt for this prompt-handle case.
        Args:
            None.
        Returns:
            The fixed Sydney prompt, with the handle prepended for user cases.
        Raises:
            None.
        Side effects:
            None.
        Used by:
            run_probe_case when constructing chat completion messages.
        """
        if self.placement == "user":
            return f"{self.handle}\n{self.base_user_prompt}"
        return self.base_user_prompt


@dataclass(frozen=True)
class StreamedProbeResult:
    """Store collected output and timing for one streamed probe case.
    Args:
        visible_text: Assistant content produced outside reasoning_content.
        reasoning_text: Reasoning content exposed by the local endpoint.
        elapsed_seconds: Total request time in seconds.
        reasoning_seconds: Time from request start to the final reasoning chunk.
        response_seconds: Time from first visible-content chunk to the final visible-content chunk.
        reported_reasoning_tokens: Reasoning token count reported by the endpoint, if any.
        reported_completion_tokens: Completion token count reported by the endpoint, if any.
    Used by:
        stream_probe_response and print_probe_result.
    """

    visible_text: str
    reasoning_text: str
    elapsed_seconds: float
    reasoning_seconds: float | None
    response_seconds: float | None
    reported_reasoning_tokens: int | None
    reported_completion_tokens: int | None


PROMPT_HANDLE_CASES = [
    PromptHandleCase(
        "/no_think user prompt: Sydney in one day",
        "/no_think",
        "user",
        DEFAULT_USER_PROMPT,
    ),
    PromptHandleCase(
        "/no_think user prompt: Paris first-time visit",
        "/no_think",
        "user",
        "I have six hours in Paris on my first visit. What should I do?",
    ),
    PromptHandleCase(
        "/no_think user prompt: Tokyo food itinerary",
        "/no_think",
        "user",
        "Create a half-day Tokyo food itinerary for someone who loves street food.",
    ),
]


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the standalone probe.
    Args:
        None.
    Returns:
        Parsed argparse namespace containing endpoint, model, timeout, and token settings.
    Raises:
        SystemExit: Raised by argparse when command-line arguments are invalid.
    Side effects:
        Reads command-line arguments from the current process.
    Used by:
        main.
    """
    parser = argparse.ArgumentParser(
        description="Probe Qwen thinking controls against an OpenAI-compatible endpoint."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key", default=DEFAULT_API_KEY)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    return parser.parse_args()


def configure_stdout() -> None:
    """Configure stdout to tolerate Unicode returned by local models.
    Args:
        None.
    Returns:
        None.
    Raises:
        None.
    Side effects:
        Reconfigures stdout to use UTF-8 with replacement where supported.
    Used by:
        main before any probe output is printed.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")


def safe_print(text: str = "") -> None:
    """Print text immediately while tolerating console encoding limits.
    Args:
        text: Text to print to stdout.
    Returns:
        None.
    Raises:
        OSError: Raised if writing to stdout fails for non-encoding reasons.
    Side effects:
        Writes one line to stdout and flushes it.
    Used by:
        print_case_header, run_probe_case, print_probe_result, and main.
    """
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        encoded = text.encode(sys.stdout.encoding or "utf-8", errors="replace")
        print(encoded.decode(sys.stdout.encoding or "utf-8"), flush=True)


def message_attr(message: Any, attr_name: str) -> Any:
    """Read a message attribute from SDK objects or dict-like responses.
    Args:
        message: Chat completion message object or mapping to inspect.
        attr_name: Attribute or key name to retrieve.
    Returns:
        The attribute value when present; otherwise None.
    Raises:
        None.
    Side effects:
        None.
    Used by:
        response_text and reasoning_content.
    """
    if hasattr(message, attr_name):
        return getattr(message, attr_name)
    if isinstance(message, dict):
        return message.get(attr_name)
    return None


def usage_attr(usage: Any, attr_name: str) -> Any:
    """Read a usage attribute from SDK objects or dict-like responses.
    Args:
        usage: Usage object or mapping to inspect.
        attr_name: Attribute or key name to retrieve.
    Returns:
        The attribute value when present; otherwise None.
    Raises:
        None.
    Side effects:
        None.
    Used by:
        completion_token_counts.
    """
    if usage is None:
        return None
    if hasattr(usage, attr_name):
        return getattr(usage, attr_name)
    if isinstance(usage, dict):
        return usage.get(attr_name)
    return None


def completion_token_counts(usage: Any) -> tuple[int | None, int | None]:
    """Extract reported completion and reasoning token counts from usage.
    Args:
        usage: Usage object returned by a streaming chunk, if available.
    Returns:
        Tuple of completion tokens and reasoning tokens, each optional.
    Raises:
        None.
    Side effects:
        None.
    Used by:
        stream_probe_response.
    """
    completion_tokens = usage_attr(usage, "completion_tokens")
    details = usage_attr(usage, "completion_tokens_details")
    reasoning_tokens = usage_attr(details, "reasoning_tokens")
    return completion_tokens, reasoning_tokens


def compact_preview(text: str, max_chars: int = 500) -> str:
    """Create a short single-line preview for console output.
    Args:
        text: Text to compact and truncate.
        max_chars: Maximum number of characters to return.
    Returns:
        A whitespace-normalized preview, truncated with an ellipsis when needed.
    Raises:
        None.
    Side effects:
        None.
    Used by:
        run_probe_case.
    """
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3] + "..."


def estimated_token_count(text: str) -> int:
    """Estimate token count when the endpoint does not report usage.
    Args:
        text: Text to estimate.
    Returns:
        Approximate output token count based on whitespace-separated text.
    Raises:
        None.
    Side effects:
        None.
    Used by:
        print_probe_result.
    """
    return len(text.split())


def print_case_header(
    case_number: int,
    total_cases: int,
    prompt_case: PromptHandleCase,
) -> None:
    """Print the heading for one probe case.
    Args:
        case_number: One-based case number being run.
        total_cases: Total number of cases in the probe matrix.
        prompt_case: Prompt-handle test case being run.
    Returns:
        None.
    Raises:
        OSError: Raised if writing to stdout fails.
    Side effects:
        Prints the case header to stdout.
    Used by:
        main.
    """
    test_title = prompt_case.name
    safe_print("=" * 80)
    safe_print(f"{case_number}. Running test {case_number}: {test_title}")
    safe_print(f"Total tests: {total_cases}")


def stream_delta_text(delta: Any, attr_name: str) -> str:
    """Read one streamed delta text field.
    Args:
        delta: Streaming chat completion delta object or mapping.
        attr_name: Delta field name to read.
    Returns:
        Delta text, or an empty string when unavailable.
    Raises:
        None.
    Side effects:
        None.
    Used by:
        stream_probe_response.
    """
    value = message_attr(delta, attr_name)
    return value or ""


def stream_probe_response(request_args: dict[str, Any]) -> StreamedProbeResult:
    """Collect a streamed probe response and timing metrics.
    Args:
        request_args: Keyword arguments for client.chat.completions.create.
    Returns:
        StreamedProbeResult with collected text, timings, and reported token counts.
    Raises:
        Exception: Propagates OpenAI SDK or endpoint errors to the caller.
    Side effects:
        Sends one streaming chat completion request to the configured endpoint.
    Used by:
        run_probe_case.
    """
    started_at = time.perf_counter()
    reasoning_parts: list[str] = []
    visible_parts: list[str] = []
    first_response_at: float | None = None
    last_response_at: float | None = None
    last_reasoning_at: float | None = None
    reported_completion_tokens: int | None = None
    reported_reasoning_tokens: int | None = None

    stream = request_args["client"].chat.completions.create(
        **request_args["payload"]
    )
    for chunk in stream:
        now = time.perf_counter()
        usage = getattr(chunk, "usage", None)
        completion_tokens, reasoning_tokens = completion_token_counts(usage)
        if completion_tokens is not None:
            reported_completion_tokens = completion_tokens
        if reasoning_tokens is not None:
            reported_reasoning_tokens = reasoning_tokens

        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        reasoning_delta = stream_delta_text(delta, "reasoning_content")
        visible_delta = stream_delta_text(delta, "content")
        if reasoning_delta:
            reasoning_parts.append(reasoning_delta)
            last_reasoning_at = now
        if visible_delta:
            visible_parts.append(visible_delta)
            if first_response_at is None:
                first_response_at = now
            last_response_at = now

    elapsed_seconds = time.perf_counter() - started_at
    reasoning_seconds = (
        last_reasoning_at - started_at if last_reasoning_at is not None else None
    )
    response_seconds = (
        last_response_at - first_response_at
        if first_response_at is not None and last_response_at is not None
        else None
    )
    return StreamedProbeResult(
        visible_text="".join(visible_parts),
        reasoning_text="".join(reasoning_parts),
        elapsed_seconds=elapsed_seconds,
        reasoning_seconds=reasoning_seconds,
        response_seconds=response_seconds,
        reported_reasoning_tokens=reported_reasoning_tokens,
        reported_completion_tokens=reported_completion_tokens,
    )


def print_token_metric(label: str, reported: int | None, text: str) -> None:
    """Print a token metric using reported usage or an estimate fallback.
    Args:
        label: User-facing metric label.
        reported: Endpoint-reported token count, if available.
        text: Text to estimate from when reported usage is unavailable.
    Returns:
        None.
    Raises:
        OSError: Raised if writing to stdout fails for non-encoding reasons.
    Side effects:
        Prints one token metric line to stdout.
    Used by:
        print_probe_result.
    """
    if reported is not None:
        safe_print(f"{label}: {reported} (reported)")
        return
    safe_print(f"{label}: {estimated_token_count(text)} (estimated from text)")


def print_probe_result(result: StreamedProbeResult) -> None:
    """Print the completed result block for one probe case.
    Args:
        result: Streamed probe output and metrics.
    Returns:
        None.
    Raises:
        OSError: Raised if writing to stdout fails for non-encoding reasons.
    Side effects:
        Prints the result block to stdout.
    Used by:
        run_probe_case.
    """
    has_visible_think = "<think>" in result.visible_text or "</think>" in result.visible_text
    has_reasoning_content = bool(result.reasoning_text.strip())
    response_tokens = None
    if (
        result.reported_completion_tokens is not None
        and result.reported_reasoning_tokens is not None
    ):
        response_tokens = max(
            result.reported_completion_tokens - result.reported_reasoning_tokens,
            0,
        )

    safe_print("Test Result:")
    safe_print(f"Status: accepted in {result.elapsed_seconds:.2f}s")
    safe_print(f"Visible <think> marker: {'yes' if has_visible_think else 'no'}")
    safe_print(
        f"reasoning_content detected: {'yes' if has_reasoning_content else 'no'}"
    )
    print_token_metric(
        "Reasoning tokens outputted",
        result.reported_reasoning_tokens,
        result.reasoning_text,
    )
    safe_print(
        "Reasoning time taken: "
        + (
            f"{result.reasoning_seconds:.2f}s"
            if result.reasoning_seconds is not None
            else "not observed"
        )
    )
    print_token_metric("Response tokens outputted", response_tokens, result.visible_text)
    safe_print(
        "Response time taken: "
        + (
            f"{result.response_seconds:.2f}s"
            if result.response_seconds is not None
            else "not observed"
        )
    )
    if has_reasoning_content:
        safe_print(f"Reasoning preview: {compact_preview(result.reasoning_text)}")
    safe_print(f"Response preview: {compact_preview(result.visible_text)}")


def run_probe_case(
    client: OpenAI,
    model: str,
    max_tokens: int,
    prompt_case: PromptHandleCase,
) -> None:
    """Run one thinking-control probe case and print the result.
    Args:
        client: OpenAI SDK client configured for the local LM Studio endpoint.
        model: Model identifier to pass to the chat completions API.
        max_tokens: Maximum response tokens for the test request.
        prompt_case: Prompt-handle test case for the current request.
    Returns:
        None.
    Raises:
        OSError: Raised if writing to stdout fails.
    Side effects:
        Sends one chat completion request to the configured endpoint and prints results.
    Used by:
        main.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt_case.system_prompt()},
            {"role": "user", "content": prompt_case.user_prompt()},
        ],
        "temperature": 0.7,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    started_at = time.perf_counter()
    try:
        result = stream_probe_response({"client": client, "payload": payload})
    except Exception as exc:
        elapsed = time.perf_counter() - started_at
        safe_print("Test Result:")
        safe_print(f"Status: rejected after {elapsed:.2f}s")
        safe_print(f"Error: {type(exc).__name__}: {exc}")
        return

    print_probe_result(result)


def main() -> None:
    """Run the full thinking-control probe matrix.
    Args:
        None.
    Returns:
        None.
    Raises:
        SystemExit: Raised by parse_args when command-line arguments are invalid.
        OSError: Raised if writing to stdout fails.
    Side effects:
        Creates an OpenAI SDK client, calls the configured local endpoint, and prints results.
    Used by:
        The module entrypoint when this file is executed as a script.
    """
    configure_stdout()
    args = parse_args()
    client = OpenAI(
        base_url=args.base_url,
        api_key=args.api_key,
        timeout=args.timeout,
    )

    total_cases = len(PROMPT_HANDLE_CASES)
    safe_print("Qwen thinking-control probe")
    safe_print(f"Endpoint: {args.base_url}")
    safe_print(f"Model: {args.model}")
    safe_print("User prompts: see each test title")
    safe_print(f"Total cases: {total_cases}")

    case_number = 0
    for prompt_case in PROMPT_HANDLE_CASES:
        case_number += 1
        print_case_header(case_number, total_cases, prompt_case)
        run_probe_case(
            client=client,
            model=args.model,
            max_tokens=args.max_tokens,
            prompt_case=prompt_case,
        )


if __name__ == "__main__":
    main()
