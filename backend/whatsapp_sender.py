"""
WhatsApp Cloud API sender for Alpha Lens.

Reads creds from env:
  WHATSAPP_PHONE_NUMBER_ID   — your "from" number's Meta ID
  WHATSAPP_ACCESS_TOKEN      — System User token (60-day) preferred
  WHATSAPP_RECIPIENTS        — comma-separated phone numbers (E.164, no '+')
                                e.g. "917799499857,919876543210"
                                These must be verified in Meta dashboard
                                while you're on the test number.

Exposes three functions used elsewhere in the app:

  send_template_message(to, template, lang='en_US', body_params=None)
    Low-level send. Returns dict with status/message_id/error.

  send_signal_alert(signal)
    High-level — formats and sends a signal-alert template to every
    recipient in WHATSAPP_RECIPIENTS, with per-ticker cooldown + per-day cap.

  send_test_message(to)
    Quick smoke test — uses Meta's pre-approved 'hello_world' template
    so you can verify plumbing before your own template is approved.

All functions are failure-tolerant: a network or Meta error is logged
but never raises into the signal worker.
"""
from __future__ import annotations

import os
import time
import threading
from datetime import datetime, timezone, timedelta

import requests


GRAPH_API_VERSION = "v21.0"  # Bumped periodically by Meta; v21 stable as of 2025
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# When we ship a real approved template, change this. Until then we use
# Meta's universal pre-approved hello_world for plumbing checks.
DEFAULT_TEMPLATE = os.environ.get("WHATSAPP_SIGNAL_TEMPLATE", "hello_world")

# Cooldowns to prevent the worker firing 50 alerts on a stock with a noisy
# news day. Module-level in-memory state — resets on each Render redeploy
# which is fine; lost cooldowns just mean a few extra messages tomorrow.
_COOLDOWN_LOCK = threading.Lock()
_TICKER_LAST_SENT: dict[str, float] = {}      # ticker -> unix ts of last alert
_RECIPIENT_DAY_COUNT: dict[str, dict] = {}     # phone -> {date_str, count}
TICKER_COOLDOWN_SECS = 30 * 60                 # 30 min per ticker
PER_RECIPIENT_DAILY_CAP = 8                    # max 8 alerts/day to one phone


def _env(key: str) -> str:
    return (os.environ.get(key) or "").strip()


def _get_recipients() -> list[str]:
    raw = _env("WHATSAPP_RECIPIENTS")
    if not raw:
        return []
    return [p.strip().lstrip("+") for p in raw.split(",") if p.strip()]


def _is_configured() -> bool:
    return bool(_env("WHATSAPP_PHONE_NUMBER_ID") and _env("WHATSAPP_ACCESS_TOKEN"))


def configuration_status() -> dict:
    """Used by /api/debug-whatsapp to introspect without exposing secrets."""
    token = _env("WHATSAPP_ACCESS_TOKEN")
    return {
        "phone_number_id_set":   bool(_env("WHATSAPP_PHONE_NUMBER_ID")),
        "access_token_set":      bool(token),
        "access_token_prefix":   (token[:6] + "..." if token else None),
        "verify_token_set":      bool(_env("WHATSAPP_VERIFY_TOKEN")),
        "recipients_configured": len(_get_recipients()),
        "recipients_preview":    [r[:3] + "****" + r[-3:] for r in _get_recipients()],
        "default_template":      DEFAULT_TEMPLATE,
        "api_version":           GRAPH_API_VERSION,
    }


def _record_send_attempt(ticker: str, recipient: str):
    """Update cooldown bookkeeping. Caller already holds _COOLDOWN_LOCK."""
    now = time.time()
    if ticker:
        _TICKER_LAST_SENT[ticker] = now
    if recipient:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rec = _RECIPIENT_DAY_COUNT.get(recipient)
        if not rec or rec["date_str"] != today:
            _RECIPIENT_DAY_COUNT[recipient] = {"date_str": today, "count": 1}
        else:
            rec["count"] += 1


def _can_send(ticker: str, recipient: str) -> tuple[bool, str]:
    """Cooldown gate. Returns (ok, reason_if_blocked)."""
    with _COOLDOWN_LOCK:
        last = _TICKER_LAST_SENT.get(ticker)
        if last and (time.time() - last) < TICKER_COOLDOWN_SECS:
            return False, f"ticker_cooldown({int(TICKER_COOLDOWN_SECS - (time.time() - last))}s)"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rec = _RECIPIENT_DAY_COUNT.get(recipient)
        if rec and rec["date_str"] == today and rec["count"] >= PER_RECIPIENT_DAILY_CAP:
            return False, "daily_cap_reached"
    return True, ""


def send_template_message(to: str, template: str,
                           lang: str = "en_US",
                           body_params: list[str] | None = None) -> dict:
    """
    Low-level template send via Meta Graph API.

    `to` is E.164 phone number WITHOUT the '+', e.g. '917799499857'.
    `body_params` is a list of strings that fill {{1}}, {{2}}, ... in the template.

    Returns dict with one of:
      {'ok': True, 'message_id': 'wamid....'}
      {'ok': False, 'error': '...', 'status': <int>}
    """
    if not _is_configured():
        return {"ok": False, "error": "WhatsApp not configured (env vars missing)"}

    phone_id = _env("WHATSAPP_PHONE_NUMBER_ID")
    token    = _env("WHATSAPP_ACCESS_TOKEN")
    url      = f"{GRAPH_BASE}/{phone_id}/messages"

    payload: dict = {
        "messaging_product": "whatsapp",
        "to": to.lstrip("+"),
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": lang},
        },
    }
    if body_params:
        payload["template"]["components"] = [{
            "type": "body",
            "parameters": [{"type": "text", "text": str(p)} for p in body_params],
        }]

    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=12,
        )
    except Exception as e:
        return {"ok": False, "error": f"network: {e}"}

    if resp.status_code != 200:
        try:
            err = resp.json().get("error", {})
            return {
                "ok": False,
                "status": resp.status_code,
                "error": err.get("message") or resp.text[:200],
                "code": err.get("code"),
                "type": err.get("type"),
            }
        except Exception:
            return {"ok": False, "status": resp.status_code, "error": resp.text[:200]}

    data = resp.json() or {}
    msgs = data.get("messages") or []
    return {
        "ok": True,
        "message_id": msgs[0].get("id") if msgs else None,
        "to": data.get("contacts", [{}])[0].get("wa_id"),
    }


def send_test_message(to: str) -> dict:
    """Smoke-test with Meta's pre-approved hello_world template."""
    return send_template_message(to, template="hello_world", lang="en_US")


def send_signal_alert(signal: dict) -> dict:
    """
    Fan-out a signal alert to every recipient in WHATSAPP_RECIPIENTS.

    `signal` dict expected to have at least:
        ticker, direction, confidence, target_pct, stop_pct, headline

    Returns summary dict for logs:
        {recipients_attempted, sent, blocked_cooldown, errors}
    """
    if not _is_configured():
        return {"ok": False, "error": "WhatsApp not configured"}

    recipients = _get_recipients()
    if not recipients:
        return {"ok": False, "error": "WHATSAPP_RECIPIENTS env var is empty"}

    ticker     = (signal.get("ticker") or "").upper()
    base       = ticker.replace(".NS", "").replace(".BO", "")
    direction  = signal.get("direction") or "BULLISH"
    confidence = signal.get("confidence") or 0
    target_pct = signal.get("target_pct") or 2.0
    stop_pct   = signal.get("stop_pct") or 1.0
    headline   = (signal.get("headline") or "")[:55]

    body_params = [
        base,
        direction,
        str(confidence),
        f"+{target_pct:.1f}%",
        f"-{stop_pct:.1f}%",
        headline,
    ]

    summary = {"recipients_attempted": 0, "sent": 0, "blocked_cooldown": 0, "errors": []}

    for phone in recipients:
        summary["recipients_attempted"] += 1
        ok, reason = _can_send(base, phone)
        if not ok:
            summary["blocked_cooldown"] += 1
            print(f"[WA] alert to {phone[-4:]} blocked: {reason}", flush=True)
            continue

        # While custom template is pending approval, fall back to hello_world.
        # The body_params will be ignored for hello_world (it has no variables).
        result = send_template_message(
            phone,
            template=DEFAULT_TEMPLATE,
            body_params=body_params if DEFAULT_TEMPLATE != "hello_world" else None,
        )
        if result.get("ok"):
            with _COOLDOWN_LOCK:
                _record_send_attempt(base, phone)
            summary["sent"] += 1
            print(f"[WA] alert sent {base} -> {phone[-4:]}: {result.get('message_id')}", flush=True)
        else:
            summary["errors"].append({"to": phone[-4:], **{k: v for k, v in result.items() if k != "ok"}})
            print(f"[WA] alert FAILED {base} -> {phone[-4:]}: {result.get('error')}", flush=True)

    return summary
