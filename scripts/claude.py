"""Shared Claude call: one prompt in, one JSON object out."""

import json
import os
import sys

import anthropic
from tenacity import retry, retry_if_exception

from utils import _is_anthropic_transient, ANTHROPIC_RETRY_KWARGS, strip_code_fence, temp_path

MODEL = "claude-sonnet-5-5"

_anthropic_retry = retry(retry=retry_if_exception(_is_anthropic_transient), **ANTHROPIC_RETRY_KWARGS)


@_anthropic_retry
def _call_claude(client, system, messages, max_tokens):
    return client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
    )


def generate_json(system, prompt, max_tokens, raw_name):
    """Call Claude and parse its reply as JSON. The raw reply is saved to RUNNER_TEMP/raw_name."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=0)
    message = _call_claude(client, system, [{"role": "user", "content": prompt}], max_tokens)

    raw = "".join(block.text for block in message.content if block.type == "text")
    print(f"Claude response: {len(raw):,} chars | stop_reason={message.stop_reason}")
    with open(temp_path(raw_name), "w", encoding="utf-8") as f:
        f.write(raw)

    if message.stop_reason == "max_tokens":
        raise RuntimeError(f"Claude response truncated (max_tokens={max_tokens})")
    try:
        return json.loads(strip_code_fence(raw))
    except json.JSONDecodeError as e:
        print(f"Raw response saved to {temp_path(raw_name)}", file=sys.stderr)
        raise RuntimeError(f"Claude response is not valid JSON: {e}") from e
