"""Production-safe API request/response logging with token metrics.

Provides session-wise logging of full request/response data and token metrics
in JSONL format. Supports three modes:
- disabled: No logging (default, no overhead)
- redacted: Full logging with secrets masked (production-safe)
- debug: Full logging without redaction (development only)

Output files in logs_dir/:
- api_requests_<session_id>_<date>.jsonl
- api_token_metrics_<session_id>_<date>.jsonl
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def log_api_request(
    agent: Any,
    request_payload: Dict[str, Any],
    response: Optional[Any],
    duration_ms: float,
    error: Optional[Exception] = None,
) -> None:
    """Log full API request/response with sensitive data redaction.

    Args:
        agent: AIAgent instance with logging config and logs_dir
        request_payload: Full request dict (api_kwargs)
        response: Response object from provider (may be None on error)
        duration_ms: API call duration in milliseconds
        error: Exception if request failed (None if success)
    """
    if not agent or agent._api_request_logging == "disabled":
        return

    # Only log errors if errors_only mode is enabled and this is not an error
    if (
        agent._api_request_logging == "redacted"
        and agent._api_request_logging_errors_only
        and error is None
    ):
        return

    # Skip redaction in debug mode
    skip_redaction = agent._api_request_logging == "debug"

    # Locate the request sub-payload. Anthropic-style calls pass tools/system/
    # messages as flat kwargs; some transports nest them under "body".
    body = request_payload.get("body") if isinstance(request_payload.get("body"), dict) else request_payload

    # Extract response data
    response_status_code = None
    response_headers = {}
    response_body = None

    if response is not None:
        # Handle response-like objects (from various providers)
        if hasattr(response, "status_code"):
            response_status_code = response.status_code
        elif hasattr(response, "status"):
            # Some providers use .status instead
            response_status_code = getattr(response, "status", None)

        # Extract headers if available
        if hasattr(response, "headers"):
            response_headers = dict(response.headers) if response.headers else {}

        # Extract body (varies by provider)
        if hasattr(response, "__dict__"):
            # Try to extract the full response as a dict
            try:
                response_body = _serialize_response(response)
            except Exception:
                response_body = {"error": "could not serialize response"}

    # Build log entry
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "turn_id": getattr(agent, "turn_id", None),
        "api_call_count": getattr(agent, "_api_call_count", 0),
        "request": {
            "method": request_payload.get("method", "POST"),
            "url": request_payload.get("url", "unknown"),
            "headers": dict(request_payload.get("headers", {})) if not skip_redaction else dict(request_payload.get("headers", {})),
            "body": dict(request_payload.get("body", {})) if "body" in request_payload else request_payload.get("messages", []),
            # The tool schemas are the single biggest prompt-token contributor
            # but were previously omitted from the log, making the recorded body
            # look far smaller than what the provider billed (issue #17). Capture
            # them (redacted + truncated downstream) so the true split is visible.
            "tools": body.get("tools") if isinstance(body, dict) else None,
            "system": body.get("system") if isinstance(body, dict) else None,
        },
        "response": {
            "status_code": response_status_code,
            "headers": response_headers,
            "body": response_body,
        } if response is not None else None,
        "duration_ms": duration_ms,
        "status": "error" if error else "success",
        "error": str(error) if error else None,
    }

    # Redact sensitive fields if not in debug mode
    if not skip_redaction:
        entry["request"] = _redact_sensitive_fields(entry["request"])
        if entry["response"]:
            entry["response"] = _redact_sensitive_fields(entry["response"])

    # Truncate large fields
    entry = _truncate_large_fields(entry)

    # Write to log file (JSONL format - one JSON per line)
    log_path = _get_session_log_path(agent, "requests")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            json.dump(entry, f, default=str)
            f.write("\n")
    except Exception as e:
        logger.warning(f"Failed to write API request log to {log_path}: {e}")


def log_token_metrics(
    agent: Any,
    usage: Optional[Any],
    model: str,
    provider: str,
    cost_usd: Optional[float],
    duration_ms: float,
    error: Optional[Exception] = None,
    request_payload: Optional[Dict[str, Any]] = None,
) -> None:
    """Log token usage metrics and cost estimation.

    Args:
        agent: AIAgent instance with logging config and logs_dir
        usage: Usage object with token counts (input_tokens, output_tokens, etc.)
        model: Model name/identifier
        provider: Provider name (anthropic, openai, etc.)
        cost_usd: Estimated cost in USD
        duration_ms: API call duration in milliseconds
        error: Exception if request failed (None if success)
        request_payload: The request kwargs actually sent. Used to compute a
            local tools/system/messages token estimate when the provider only
            returns the aggregate ``prompt_tokens`` (e.g. ollama-cloud) so the
            ``request_tokens`` breakdown is populated rather than all-zero
            (issue #17).
    """
    if not agent or not agent._api_token_metrics_logging:
        return

    # Parse token counts from usage object
    usage_dict = _extract_usage_dict(usage)

    # When the provider doesn't break the prompt down by section, estimate the
    # split locally from the payload we sent. Never overwrite a real value the
    # provider supplied — only fill zeros/missing fields.
    estimated_breakdown = {}
    if request_payload is not None:
        est = _estimate_request_token_breakdown(request_payload)
        for key in ("tools_tokens", "system_tokens", "messages_tokens"):
            if not usage_dict.get(key):
                usage_dict[key] = est.get(key, 0)
                if est.get(key):
                    estimated_breakdown[key] = est[key]

    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "turn_id": getattr(agent, "turn_id", None),
        "api_call_count": getattr(agent, "_api_call_count", 0),
        "model": model,
        "provider": provider,
        "request_tokens": {
            "input_tokens": usage_dict.get("input_tokens", 0),
            "system_tokens": usage_dict.get("system_tokens", 0),
            "messages_tokens": usage_dict.get("messages_tokens", 0),
            "tools_tokens": usage_dict.get("tools_tokens", 0),
        },
        # Names of the request_tokens fields that were locally estimated rather
        # than reported by the provider (issue #17 observability).
        "request_tokens_estimated": sorted(estimated_breakdown.keys()),
        "response_tokens": {
            "output_tokens": usage_dict.get("output_tokens", 0),
            "reasoning_tokens": usage_dict.get("reasoning_tokens", 0),
            "cache_read_tokens": usage_dict.get("cache_read_tokens", 0),
            "cache_write_tokens": usage_dict.get("cache_write_tokens", 0),
        },
        "totals": {
            "prompt_tokens": usage_dict.get("prompt_tokens") or (usage_dict.get("input_tokens", 0) + usage_dict.get("system_tokens", 0)),
            "total_tokens": usage_dict.get("total_tokens") or sum([
                usage_dict.get("input_tokens", 0),
                usage_dict.get("output_tokens", 0),
            ]),
            "cached_tokens": usage_dict.get("cache_read_tokens", 0) + usage_dict.get("cache_write_tokens", 0),
        },
        "cost_usd": cost_usd,
        "duration_ms": duration_ms,
        "status": "error" if error else "success",
        "error": str(error) if error else None,
    }

    # Write to log file (JSONL format - one JSON per line)
    log_path = _get_session_log_path(agent, "metrics")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            json.dump(entry, f, default=str)
            f.write("\n")
    except Exception as e:
        logger.warning(f"Failed to write API token metrics log to {log_path}: {e}")


def _redact_sensitive_fields(payload: Any) -> Any:
    """Redact secrets from payload before logging.

    Uses existing redact_sensitive_text utility, plus field-specific truncation.
    """
    if not isinstance(payload, dict):
        return payload

    try:
        from agent.redact import redact_sensitive_text

        # Serialize, redact, deserialize
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
        redacted_json = json.loads(redact_sensitive_text(serialized, force=True))

        # Additional field truncation for large content
        if isinstance(redacted_json, dict):
            if "body" in redacted_json and isinstance(redacted_json["body"], dict):
                if "system" in redacted_json["body"]:
                    system = redacted_json["body"]["system"]
                    system_str = str(system)
                    if len(system_str) > 10000:
                        redacted_json["body"]["system"] = f"[redacted system prompt - {len(system_str)} bytes]"

        return redacted_json
    except Exception as e:
        logger.warning(f"Failed to redact sensitive fields: {e}")
        return payload


def _truncate_large_fields(entry: Dict[str, Any], max_size: int = 10000) -> Dict[str, Any]:
    """Truncate large fields to prevent log bloat.

    Args:
        entry: Log entry dict
        max_size: Maximum size for content fields in bytes

    Returns:
        Entry with large fields truncated
    """
    def _truncate_value(val: Any) -> Any:
        if isinstance(val, str):
            val_str = val
        elif isinstance(val, (dict, list)):
            val_str = json.dumps(val, default=str)
        else:
            val_str = str(val)

        if len(val_str) > max_size:
            return f"[truncated - {len(val_str)} bytes, showing first {max_size} chars]\n{val_str[:max_size]}"
        return val

    # Recursively truncate large fields
    def _process_dict(d: Dict[str, Any]) -> Dict[str, Any]:
        result = {}
        for k, v in d.items():
            if isinstance(v, dict):
                result[k] = _process_dict(v)
            elif isinstance(v, str) and len(v) > max_size:
                result[k] = _truncate_value(v)
            else:
                result[k] = v
        return result

    return _process_dict(entry)


def _get_session_log_path(agent: Any, log_type: str) -> Path:
    """Get session-specific log file path.

    Args:
        agent: AIAgent instance
        log_type: 'requests' or 'metrics'

    Returns:
        Path to session-specific log file
    """
    try:
        from run_agent import _safe_session_filename_component
        safe_sid = _safe_session_filename_component(agent.session_id)
    except Exception:
        # Fallback if _safe_session_filename_component not available
        safe_sid = (agent.session_id or "unknown")[:16].replace("/", "-")

    date_str = datetime.now().strftime("%Y%m%d")
    log_filename = f"api_{log_type}_{safe_sid}_{date_str}.jsonl"
    return agent.logs_dir / log_filename


def _extract_usage_dict(usage: Optional[Any]) -> Dict[str, int]:
    """Extract token counts from usage object.

    Handles various provider usage response formats.
    """
    if not usage:
        return {}

    result = {}

    # Standard fields that most providers support
    fields = [
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "total_tokens",
        "system_tokens",
        "messages_tokens",
        "tools_tokens",
        "reasoning_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    ]

    for field in fields:
        if hasattr(usage, field):
            val = getattr(usage, field, None)
            if val is not None:
                result[field] = int(val)
        elif isinstance(usage, dict) and field in usage:
            result[field] = int(usage[field])

    return result


def _estimate_request_token_breakdown(request_payload: Dict[str, Any]) -> Dict[str, int]:
    """Estimate tools/system/messages token counts from the sent payload.

    Providers like ollama-cloud only return the aggregate ``prompt_tokens``,
    leaving the ``request_tokens`` breakdown all-zero (issue #17). This gives a
    provider-independent local estimate. Tool schemas reuse the same chars/4
    estimator that gates tool-search deferral so the two numbers are directly
    comparable; system + messages use the same rule of thumb.
    """
    if not isinstance(request_payload, dict):
        return {}
    body = request_payload.get("body") if isinstance(request_payload.get("body"), dict) else request_payload
    if not isinstance(body, dict):
        return {}

    result: Dict[str, int] = {}
    try:
        from tools.tool_search import estimate_tokens_from_schemas, CHARS_PER_TOKEN
    except Exception:
        return {}

    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        result["tools_tokens"] = estimate_tokens_from_schemas(tools)

    def _chars_to_tokens(value: Any) -> int:
        if value is None:
            return 0
        try:
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
        return int(len(text) / CHARS_PER_TOKEN) if text else 0

    system = body.get("system")
    if system:
        result["system_tokens"] = _chars_to_tokens(system)

    messages = body.get("messages")
    if isinstance(messages, list) and messages:
        result["messages_tokens"] = _chars_to_tokens(messages)

    return result


def _serialize_response(response: Any) -> Dict[str, Any]:
    """Serialize response object to dict for logging.

    Attempts to extract relevant fields while handling various response types.
    """
    if response is None:
        return None

    result = {}

    # Standard fields to extract
    fields = [
        "id",
        "type",
        "role",
        "content",
        "usage",
        "model",
        "finish_reason",
        "stop_reason",
        "status",
        "output_text",
        "output",
    ]

    for field in fields:
        if hasattr(response, field):
            val = getattr(response, field, None)
            if val is not None:
                result[field] = val

    # Handle content specially (may be list of objects)
    if "content" in result and isinstance(result["content"], list):
        result["content"] = [
            c.__dict__ if hasattr(c, "__dict__") else c for c in result["content"]
        ]

    return result
