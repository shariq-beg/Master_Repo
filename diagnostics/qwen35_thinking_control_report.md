# Can You Control Qwen3.5:9B Thinking? A 28-Case Diagnostic

> **Tested & authored by Shariq Beg · 10 May 2026 · Sydney, AU**

![Cases](https://img.shields.io/badge/Test_Cases-28-blue)
![Model](https://img.shields.io/badge/Model-Qwen3.5--9B_Q4__K__M-orange)
![Runtime](https://img.shields.io/badge/Runtime-LM_Studio_0.4.x-lightgrey)
![Answered](https://img.shields.io/badge/Cases_Answered-4%2F28-red)
![Thinking](https://img.shields.io/badge/Hidden_Reasoning-100%25_of_cases-critical)

---

## TL;DR

Across 28 systematic test cases, **Qwen3.5:9B running under LM Studio's OpenAI-compatible API produced reasoning output (`reasoning_content`) in every single case** — even when thinking was explicitly disabled via every documented method. Only **4 of 28 cases** yielded any visible answer at all. The endpoint accepted every control parameter without error, but *accepted* does not mean *honored*.

---

## Setup

| Parameter | Value |
|---|---|
| Model | `qwen/qwen3.5-9b` |
| Quantization | Q4_K_M |
| Runtime | LM Studio 0.4.x |
| Endpoint | `http://127.0.0.1:1234/v1` |
| `max_tokens` | 300 |
| User prompt (fixed) | *"What is the best way to explore Sydney in one day?"* |

---

## Objective

Determine whether Qwen3.5:9B thinking behavior can be controlled programmatically through:

- System-prompt handle commands (`/no_think`, `/no think`, `/think`)
- API body parameters (`enable_thinking`, `thinking_budget`, `chat_template_kwargs`)

**Target behavior for no-thinking mode:** low or absent `reasoning_content`, no visible `<think>` markers, and enough remaining tokens for a useful visible answer.

---

## Methodology

A 4 × 7 matrix of conditions was tested — 4 system-prompt handle variants crossed with 7 API body variants = **28 total cases**.

### System-prompt handles (4 variants)

| Handle | Notes |
|---|---|
| Baseline (none) | Standard system prompt, no handle |
| `/no_think` | Underscore variant — the documented Qwen3 soft-switch |
| `/no think` | Space variant — found in some community reports |
| `/think` | Explicit thinking-on instruction |

> **Scope note:** All handles were placed in the **system message only**. Placing `/no_think` in the **user message** — a variant that has reportedly worked for some Qwen3 users — was not tested here and is a priority candidate for follow-up.

### API body parameters (7 variants)

| Variant | Parameter sent |
|---|---|
| Baseline | No extra parameters |
| `enable_thinking=false` | Top-level request body |
| `enable_thinking=true` | Top-level request body |
| `thinking_budget=0` | Top-level request body |
| `thinking_budget=128` | Top-level request body |
| `chat_template_kwargs.enable_thinking=false` | Nested under `extra_body` |
| `chat_template_kwargs.enable_thinking=true` | Nested under `extra_body` |

---

## Key Numbers

| Metric | Value |
|---|---|
| Total test cases | 28 |
| Cases with no visible answer | 24 (85.7%) |
| Cases that produced a visible answer | **4 (14.3%)** |
| Cases with hidden reasoning (`reasoning_content`) | **28 (100%)** |

---

## The Token Budget Problem

Qwen3.5:9B generates hidden reasoning **before** producing a visible answer. Both compete for the same `max_tokens` budget. In **24 of 28 cases**, reasoning consumed the entire 300-token budget before a visible answer could begin.

**Typical failing case (24/28 runs):**
```
[■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■] reasoning: 299 tokens
[·] visible answer: 1 token  ✗
```

**Best case — Case 27:**
```
[■■■■■■■■■·] reasoning: 75 tokens
[■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■] visible answer: 225 tokens  ✓
```

> **Critical implication:** Results under `max_tokens=300` cannot be interpreted as a reliable test of thinking control. A rerun with `max_tokens≥900` is required to separate true control failures from budget starvation.

---

## The 4 Cases That Actually Answered

These are the only combinations that produced a visible response. Note the **inconsistency** — no obvious common pattern, and some results are counter-intuitive.

| Case | System Handle | API Body | Reasoning Tokens | Response Tokens |
|---|---|---|---|---|
| **27** ⭐ | `/think` | `chat_template_kwargs.enable_thinking=false` | 75 | **225** |
| 10 | `/no_think` | `enable_thinking=true` | 125 | 175 |
| 23 | `/think` | `enable_thinking=false` | 161 | 139 |
| 8 | `/no_think` | baseline | 214 | 86 |

> ⭐ **Case 27** was the best performer: the paradoxical combination of `/think` (handle telling the model to think) + `enable_thinking=false` (parameter telling it not to) produced the least reasoning overhead and the most visible output. The mechanism is not well understood and should not be relied upon.

> **Counter-intuitive finding:** `/no_think + enable_thinking=false` (Case 9) produced *zero* visible output, while `/no_think + enable_thinking=true` (Case 10) was one of only four cases that answered. Parameters appear to interact with the chat template in non-obvious, undocumented ways.

---

## Full Results — All 28 Cases

> ✓ = produced a visible answer &nbsp;&nbsp; ✗ = reasoning exhausted the token budget

| Case | System Handle | API Body | Total (s) | Reason Tokens | Resp Tokens | Answered |
|---|---|---|---|---|---|---|
| 1 | baseline | baseline | 24.3 | 299 | 1 | ✗ |
| 2 | baseline | `enable_thinking=false` | 26.8 | 299 | 1 | ✗ |
| 3 | baseline | `enable_thinking=true` | 29.3 | 299 | 1 | ✗ |
| 4 | baseline | `thinking_budget=0` | 29.5 | 299 | 1 | ✗ |
| 5 | baseline | `thinking_budget=128` | 30.0 | 299 | 1 | ✗ |
| 6 | baseline | `ct_kwargs.enable_thinking=false` | 30.7 | 299 | 1 | ✗ |
| 7 | baseline | `ct_kwargs.enable_thinking=true` | 31.0 | 299 | 1 | ✗ |
| **8** | `/no_think` | baseline | 30.6 | 214 | 86 | **✓** |
| 9 | `/no_think` | `enable_thinking=false` | 31.6 | 299 | 1 | ✗ |
| **10** | `/no_think` | `enable_thinking=true` | 30.5 | 125 | 175 | **✓** |
| 11 | `/no_think` | `thinking_budget=0` | 31.6 | 299 | 1 | ✗ |
| 12 | `/no_think` | `thinking_budget=128` | 34.1 | 299 | 1 | ✗ |
| 13 | `/no_think` | `ct_kwargs.enable_thinking=false` | 30.5 | 299 | 1 | ✗ |
| 14 | `/no_think` | `ct_kwargs.enable_thinking=true` | 31.0 | 299 | 1 | ✗ |
| 15 | `/no think` | baseline | 30.4 | 299 | 1 | ✗ |
| 16 | `/no think` | `enable_thinking=false` | 30.5 | 299 | 1 | ✗ |
| 17 | `/no think` | `enable_thinking=true` | 30.3 | 299 | 1 | ✗ |
| 18 | `/no think` | `thinking_budget=0` | 30.2 | 299 | 1 | ✗ |
| 19 | `/no think` | `thinking_budget=128` | 30.4 | 299 | 1 | ✗ |
| 20 | `/no think` | `ct_kwargs.enable_thinking=false` | 30.1 | 299 | 1 | ✗ |
| 21 | `/no think` | `ct_kwargs.enable_thinking=true` | 30.2 | 299 | 1 | ✗ |
| 22 | `/think` | baseline | 30.5 | 299 | 1 | ✗ |
| **23** | `/think` | `enable_thinking=false` | 30.3 | 161 | 139 | **✓** |
| 24 | `/think` | `enable_thinking=true` | 31.1 | 299 | 1 | ✗ |
| 25 | `/think` | `thinking_budget=0` | 32.0 | 299 | 1 | ✗ |
| 26 | `/think` | `thinking_budget=128` | 30.5 | 299 | 1 | ✗ |
| **27** ⭐ | `/think` | `ct_kwargs.enable_thinking=false` | 30.2 | **75** | **225** | **✓** |
| 28 | `/think` | `ct_kwargs.enable_thinking=true` | 30.4 | 299 | 1 | ✗ |

---

## Key Findings

**1. Reasoning appeared in all 28 cases.**
Even with every documented disable method applied, `reasoning_content` was present in every API response. Thinking was never truly off — it was merely hidden from the visible response text.

**2. Qwen3.5 dropped the `/think` / `/nothink` soft-switches.**
Qwen3 (the prior version) officially supported `/think` and `/no_think` as in-prompt toggle commands. Qwen3.5 removed support for these. The endpoint accepts them silently without error — giving false confidence that the command was processed.

**3. The `/no think` variant (space, not underscore) never worked.**
All 7 cases using `/no think` failed entirely. The correct token for Qwen3 was always `/no_think` with an underscore. The space variant was never valid.

**4. Token budget starvation was the primary failure mode.**
In 24 of 28 cases, reasoning consumed exactly 299 of 300 available tokens, leaving only 1 token for the visible answer. This makes it impossible to conclude whether the control parameters were truly ineffective or simply overwhelmed by reasoning before they could take effect.

**5. Parameter interactions are unpredictable.**
Some combinations that logically should disable thinking (e.g. `/no_think + enable_thinking=false`) produced worse results than combinations that shouldn't (e.g. `/think + enable_thinking=false`). LM Studio appears to apply these parameters inconsistently or in an undocumented order of precedence.

---

## Recommended Next Steps

**Step 1 — Rerun with a larger token budget**

Repeat a subset of cases with `max_tokens=900` or higher. This is the single most important follow-up — it separates true control failures from budget starvation.

**Step 2 — Test the closest-to-working pattern (Case 27)**

```python
response = client.chat.completions.create(
    model="qwen/qwen3.5-9b",
    messages=[
        {
            "role": "system",
            "content": "/think You are a tourist guide and trip planning expert..."
        },
        {
            "role": "user",
            "content": "What is the best way to explore Sydney in one day?"
        }
    ],
    max_tokens=900,   # <-- critical change
    extra_body={
        "chat_template_kwargs": {
            "enable_thinking": False
        }
    }
)
```

**Step 3 — Try `/no_think` in the user message**

Community reports suggest this location is more reliable than the system prompt for Qwen3. Not tested in this run.

**Other variables worth testing:**

- Different serving backend (Ollama, vLLM, llama.cpp) — LM Studio's chat template handling may be the root cause
- vLLM with `--enable-reasoning-parser` removed entirely
- Different GGUF quantization (Q8_0, F16)
- Higher quantization may follow the chat template more faithfully

---

## Conclusion

Under LM Studio 0.4.x with a Q4_K_M GGUF, **Qwen3.5:9B does not offer reliable programmatic thinking control** using the parameters tested here. Every control was silently accepted and silently ignored. The model always reasoned, and in most cases that reasoning consumed the entire output budget before a visible answer could be produced.

**Practical takeaway for developers:** If you are running Qwen3.5 locally, treat hidden reasoning as always-on and size your `max_tokens` accordingly. Do not assume `enable_thinking=false` or `/no_think` will suppress it. Monitor `reasoning_content` token usage and visible response token availability as your primary operational signals.

This experiment also highlights a **documentation gap**: Qwen3.5 dropped the soft-switch mechanism that Qwen3 supported, but the parameters are still accepted by some endpoints without raising errors. Community follow-up is needed to verify whether this behavior is specific to LM Studio's chat template implementation or is a broader property of the model weights.

---

## Limitations

- One model (Qwen3.5:9B Q4_K_M), one runtime (LM Studio 0.4.x), one fixed prompt, one `max_tokens` setting
- Results may differ with a different LM Studio build, quantization level, chat template, or serving backend
- Because the 300-token budget caused saturation in 24/28 cases, conclusions about true thinking suppression remain **tentative** until a higher-token rerun is completed
- `/no_think` was only tested in the system message, not the user message

---

## Related Discussions

- [QwenLM/Qwen3 Discussion #1329 — Can't disable thinking with /nothink](https://github.com/QwenLM/Qwen3/discussions/1329)
- [QwenLM/Qwen3 Discussion #1300 — How to set enable_thinking=False in vLLM](https://github.com/QwenLM/Qwen3/discussions/1300)
- [lmstudio-ai/lmstudio-bug-tracker Issue #1559 — Qwen3.5 thinking/non-thinking switch](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1559)
- [r/LocalLLaMA](https://www.reddit.com/r/LocalLLaMA/)

---

*Tested & authored by **Shariq Beg** · 10 May 2026 · Sydney, AU*
