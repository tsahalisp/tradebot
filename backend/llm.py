"""Minimal Anthropic Haiku helper for TradeBot's AI modules. Reuses the same
Keychain key Doros uses (service ANTHROPIC_API_KEY, account doros). Sync (requests),
since TradeBot endpoints are sync. Grounded-only: callers pass the real numbers and
instruct the model to invent nothing."""
import subprocess
import requests

_MODEL = "claude-haiku-4-5-20251001"
_URL = "https://api.anthropic.com/v1/messages"
_cache = {"k": None}


def _key():
    if _cache["k"]:
        return _cache["k"]
    try:
        k = subprocess.check_output(
            ["security", "find-generic-password", "-s", "ANTHROPIC_API_KEY", "-a", "doros", "-w"],
            text=True, stderr=subprocess.DEVNULL).strip()
        _cache["k"] = k or None
        return _cache["k"]
    except Exception:
        return None


def haiku(system: str, user: str, max_tokens: int = 600) -> str:
    key = _key()
    if not key:
        raise RuntimeError("Anthropic key not found in Keychain (ANTHROPIC_API_KEY / doros).")
    r = requests.post(_URL, timeout=30, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json",
    }, json={"model": _MODEL, "max_tokens": max_tokens, "system": system,
             "messages": [{"role": "user", "content": user}]})
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
