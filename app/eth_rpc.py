"""Safe RPC error classification. Provider bodies and credential-bearing URLs stay private."""
import math
import time
from email.utils import parsedate_to_datetime


class EthereumRPCError(RuntimeError):
    def __init__(self, message, category="unavailable", retry_after=0):
        super().__init__(message)
        self.category = category
        self.retry_after = retry_after


def retry_after(value, now=None):
    if not value:
        return 0
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            seconds = parsedate_to_datetime(value).timestamp() - (time.time() if now is None else now)
        except (ValueError, TypeError, OverflowError):
            return 0
    return max(0, math.ceil(seconds)) if math.isfinite(seconds) else 0


def rpc_result(response):
    """Parse JSON-RPC errors even on HTTP 400/403; never expose a server's raw message."""
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    error = body.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    message = str(error.get("message", "") if isinstance(error, dict) else error or "").lower()
    status = response.status_code
    if 200 <= status < 300 and error is None and body.get("result") is not None:
        return body["result"]
    detail = f"HTTP {status}" + (f", RPC {code}" if isinstance(code, int) else "")
    wait = retry_after(response.headers.get("Retry-After"))
    # Authorization and rate limits must not trigger exponential subdivision of getLogs.
    if status in (401, 403) or any(s in message for s in ("personal token", "api key required", "unauthorized")):
        category, reason = "access", "доступ к RPC/архиву ограничен; нужен разрешённый endpoint"
    elif status == 429 or any(s in message for s in ("rate limit", "rate exceeded", "too many requests", "quota exceeded", "daily limit")):
        category, reason = "rate_limit", "лимит RPC; ожидаем разрешённый повтор"
    elif code == -32601 or "method not found" in message or "method is not supported" in message:
        category, reason = "unsupported", "RPC не поддерживает этот метод"
    elif any(s in message for s in ("too many results", "query returned more", "response size",
                                   "maximum block range", "limited to a", "block range limit",
                                   "block range is too", "exceeds max", "exceed the maximum block",
                                   "please limit the query", "range should be less")):
        category, reason = "range", "слишком большой диапазон или ответ getLogs"
    elif any(s in message for s in ("missing trie node", "historical state", "pruned", "archive requests")):
        category, reason = "access", "исторические данные недоступны на этом RPC"
    else:
        category, reason = "unavailable", "RPC временно не ответил корректно"
    raise EthereumRPCError(f"{reason} ({detail})", category, wait)
