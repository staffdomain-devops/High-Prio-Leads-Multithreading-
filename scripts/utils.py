import json
import os
import re
from datetime import datetime, timezone

from tenacity import (
    RetryCallState,
    stop_after_attempt,
    stop_after_delay,
    wait_random_exponential,
)


def runner_temp():
    return os.environ.get("RUNNER_TEMP", ".")


def temp_path(name):
    return os.path.join(runner_temp(), name)


def read_json(name):
    with open(temp_path(name), encoding="utf-8") as f:
        return json.load(f)


def write_json(name, data):
    with open(temp_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def is_dry_run():
    return os.environ.get("DRY_RUN", "false").lower() == "true"


def set_output(key, value):
    """Expose a value to later GitHub Actions jobs (no-op outside Actions)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")
    print(f"output {key}={value}")


def write_dlq(opportunity_id, contact_id, failed_step, error_message, retry_count):
    record = {
        "opportunity_id": str(opportunity_id),
        "contact_id": str(contact_id or ""),
        "failed_step": str(failed_step),
        "error_message": str(error_message)[:2000],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "retry_count": retry_count,
    }
    with open(temp_path("failed_run.json"), "w") as f:
        json.dump(record, f, indent=2)


def parse_ts(value):
    """HubSpot timestamp (ISO string or epoch ms) -> epoch ms. None if empty."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or str(value).isdigit():
        return int(value)
    return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)


def strip_code_fence(text):
    text = text.strip()
    match = re.match(r'^```(?:json)?\s*([\s\S]*?)```\s*$', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r'(\{[\s\S]*\})', text)
    if match:
        return match.group(1).strip()
    return text


def substitute_tokens(template, tokens):
    def replacer(match):
        key = match.group(1)
        return str(tokens.get(key, ""))
    return re.sub(r"\{\{([^}]+)\}\}", replacer, template)


def clean_text(text):
    if not isinstance(text, str):
        return text
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"  +", " ", text)
    return text.strip()


def _is_requests_transient(exc):
    try:
        import requests as _r
        if isinstance(exc, _r.exceptions.HTTPError):
            code = exc.response.status_code
            return code == 429 or code >= 500
        if isinstance(exc, (_r.exceptions.ConnectionError, _r.exceptions.Timeout)):
            return True
    except ImportError:
        pass
    return False


def _is_anthropic_transient(exc):
    try:
        import anthropic as _a
        if isinstance(exc, _a.APIStatusError):
            return exc.status_code == 429 or exc.status_code >= 500
        if isinstance(exc, _a.APIConnectionError):
            return True
    except ImportError:
        pass
    return False


class RetryAfterWait:
    """Interprets Retry-After as seconds (Anthropic / standard HTTP)."""
    def __call__(self, retry_state: RetryCallState) -> float:
        exc = retry_state.outcome.exception()
        header_val = None
        if hasattr(exc, "response") and hasattr(exc.response, "headers"):
            header_val = exc.response.headers.get("Retry-After")
        try:
            return max(float(header_val), 0.0) if header_val else 0.0
        except (TypeError, ValueError):
            return 0.0


class HubSpotRetryAfterWait:
    """Interprets Retry-After as milliseconds (HubSpot sends ms, not seconds)."""
    def __call__(self, retry_state: RetryCallState) -> float:
        exc = retry_state.outcome.exception()
        header_val = None
        if hasattr(exc, "response") and hasattr(exc.response, "headers"):
            header_val = exc.response.headers.get("Retry-After")
        try:
            return max(float(header_val) / 1000.0, 0.0) if header_val else 0.0
        except (TypeError, ValueError):
            return 0.0


def _hs_combined_wait(retry_state):
    return max(
        wait_random_exponential(multiplier=1, min=1, max=60)(retry_state),
        HubSpotRetryAfterWait()(retry_state),
    )


def _anthropic_combined_wait(retry_state):
    return max(
        wait_random_exponential(multiplier=1, min=1, max=60)(retry_state),
        RetryAfterWait()(retry_state),
    )


REQ_RETRY_KWARGS = dict(
    wait=_hs_combined_wait,
    stop=stop_after_attempt(6) | stop_after_delay(60),
    reraise=True,
)

ANTHROPIC_RETRY_KWARGS = dict(
    wait=_anthropic_combined_wait,
    stop=stop_after_attempt(6) | stop_after_delay(60),
    reraise=True,
)


def safe_truncate(text: str, max_chars: int) -> str:
    if not isinstance(text, str):
        return text
    return text[:max_chars] if len(text) > max_chars else text
