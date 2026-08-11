"""
REPOSEA ALERTS — alerts.py
================================================
Two-week automated escalation email.

Business rule:
  A container that has been past its Last Free Day (LFD) for
  ESCALATION_THRESHOLD_DAYS (default 14 = "2 weeks") is a serious,
  compounding financial exposure. This module notifies the person
  responsible for that container's trade lane so they can take action
  (return the box, extend the lease, negotiate demurrage, etc).

Behavior:
  1. Read the current Gold table (data/gold/containers.json).
  2. Select every container with days_to_lfd <= -ESCALATION_THRESHOLD_DAYS.
  3. Route each one to an owner via pipeline/config/owners.json
     (per-container override > trade-lane desk > default).
  4. Group by owner and send ONE digest email per owner per run
     (never one email per container — that would spam the inbox).
  5. Track alert history in data/state/alerts_sent.json so a container
     that's already been flagged doesn't get re-emailed every hour —
     only an initial alert, then a re-escalation every RESEND_AFTER_DAYS
     (default 7) while it remains unresolved. A container that resolves
     (drops back under the threshold) is cleared, so if it becomes
     critical again later it will re-alert.

Configuration (all read from environment — set as GitHub Actions secrets):
  SMTP_HOST      required to actually send mail
  SMTP_PORT      default 587
  SMTP_USER      SMTP auth username
  SMTP_PASSWORD  SMTP auth password / app password
  SMTP_FROM      From: address (defaults to SMTP_USER)
  DASHBOARD_URL  optional, linked in the email (e.g. GitHub Pages URL)

If SMTP_HOST is not configured (e.g. a fresh fork that hasn't set secrets
yet, or a local dev run), alerts are logged in "DRY RUN" mode instead of
sent, so the pipeline never fails just because mail isn't set up.

CLI:
    python -m pipeline.alerts
"""

import json
import logging
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [ALERTS] %(message)s")

ROOT          = Path(__file__).parent.parent
GOLD_DIR      = ROOT / "data" / "gold"
STATE_DIR     = ROOT / "data" / "state"
CONFIG_FILE   = Path(__file__).parent / "config" / "owners.json"
ALERT_STATE_FILE = STATE_DIR / "alerts_sent.json"

ESCALATION_THRESHOLD_DAYS = 14   # "2 weeks" past Last Free Day
RESEND_AFTER_DAYS         = 7    # re-escalate weekly while still unresolved


# ── Config / state I/O ──────────────────────────────────────────────────────

def _load_owners() -> dict:
    if not CONFIG_FILE.exists():
        log.warning(f"No owners config at {CONFIG_FILE} — all alerts will use a fallback address")
        return {"default": {"name": "Fleet Operations", "email": None}, "trade_lanes": {}, "overrides": {}}
    return json.loads(CONFIG_FILE.read_text())


def _load_alert_state() -> dict:
    if ALERT_STATE_FILE.exists():
        try:
            return json.loads(ALERT_STATE_FILE.read_text())
        except Exception as e:
            log.warning(f"Alert state corrupt, resetting: {e}")
    return {}


def _save_alert_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    ALERT_STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def _owner_for(container: dict, owners_cfg: dict) -> dict:
    cid = container.get("container_id")
    override = owners_cfg.get("overrides", {}).get(cid)
    if override:
        return override
    lane_owner = owners_cfg.get("trade_lanes", {}).get(container.get("trade_lane"))
    if lane_owner:
        return lane_owner
    return owners_cfg.get("default", {"name": "Fleet Operations", "email": None})


# ── Selection ────────────────────────────────────────────────────────────────

def find_escalations(gold_rows: list, alert_state: dict) -> list:
    """
    Return the list of gold rows that need an email right now:
      - days_to_lfd <= -ESCALATION_THRESHOLD_DAYS, AND
      - never alerted before, OR last alert was >= RESEND_AFTER_DAYS ago.
    """
    now = datetime.now(timezone.utc)
    due = []
    for row in gold_rows:
        cid = row.get("container_id")
        dtl = float(row.get("days_to_lfd", 0) or 0)
        if dtl > -ESCALATION_THRESHOLD_DAYS:
            continue

        record = alert_state.get(cid)
        if record is None:
            due.append(row)
            continue

        try:
            last_sent = datetime.fromisoformat(record["last_alert_at"])
        except Exception:
            due.append(row)
            continue

        if (now - last_sent).total_seconds() / 86400 >= RESEND_AFTER_DAYS:
            due.append(row)

    return due


def clear_resolved(gold_rows: list, alert_state: dict) -> int:
    """Drop tracking for containers that are no longer past the threshold,
    so a fresh future breach re-alerts from day one instead of staying silent."""
    current = {
        row.get("container_id"): float(row.get("days_to_lfd", 0) or 0)
        for row in gold_rows
    }
    cleared = 0
    for cid in list(alert_state.keys()):
        dtl = current.get(cid)
        if dtl is None or dtl > -ESCALATION_THRESHOLD_DAYS:
            del alert_state[cid]
            cleared += 1
    return cleared


# ── Email composition + sending ─────────────────────────────────────────────

def _format_container_line(row: dict) -> str:
    overdue_days = abs(float(row.get("days_to_lfd", 0) or 0))
    return (
        f"  • {row.get('container_id')}  |  {row.get('trade_lane_label', row.get('trade_lane',''))}  |  "
        f"{row.get('vessel_name','?')}  →  {row.get('dest_label', row.get('dest_locode',''))}\n"
        f"      Overdue: {overdue_days:.1f} days past LFD   "
        f"Accrued penalty: ${float(row.get('burn_rate_usd',0) or 0):,.2f}   "
        f"7-day projection: ${float(row.get('projected_exposure_7d_usd',0) or 0):,.2f}\n"
        f"      Suggested action: {row.get('repo_strategy','Review and return / renegotiate')}"
    )


def _build_digest(owner_name: str, rows: list, dashboard_url: str) -> EmailMessage:
    total_exposure = sum(float(r.get("burn_rate_usd", 0) or 0) for r in rows)
    subject = f"[RepoSea] {len(rows)} container(s) {ESCALATION_THRESHOLD_DAYS}+ days overdue — action needed"

    lines = [
        f"Hi {owner_name},",
        "",
        f"{len(rows)} container(s) on your desk have been past their Last Free Day for "
        f"{ESCALATION_THRESHOLD_DAYS}+ days and need action:",
        "",
    ]
    for row in rows:
        lines.append(_format_container_line(row))
        lines.append("")

    lines.append(f"Combined accrued exposure: ${total_exposure:,.2f}")
    if dashboard_url:
        lines.append(f"\nFull fleet dashboard: {dashboard_url}")
    lines.append(
        f"\nYou'll get a reminder every {RESEND_AFTER_DAYS} days while a container "
        "remains unresolved. This stops automatically once it's returned or the "
        "contract is updated.\n\n— RepoSea Pipeline"
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg.set_content("\n".join(lines))
    return msg


def _send(msg: EmailMessage, to_email: str) -> bool:
    # GitHub Actions passes an unset secret as an empty string, not a missing
    # env var, so treat "" the same as "not configured" for every field here.
    host = os.environ.get("SMTP_HOST") or None
    port_raw = os.environ.get("SMTP_PORT") or "587"
    try:
        port = int(port_raw)
    except ValueError:
        log.warning(f"Invalid SMTP_PORT={port_raw!r}, defaulting to 587")
        port = 587
    user = os.environ.get("SMTP_USER") or None
    password = os.environ.get("SMTP_PASSWORD") or None
    sender = os.environ.get("SMTP_FROM") or user or "reposea-alerts@example.com"

    msg["From"] = sender
    msg["To"] = to_email

    if not host or not to_email:
        log.info(f"DRY RUN (no SMTP_HOST configured or no recipient) — would send to {to_email}:")
        for line in msg.get_content().splitlines():
            log.info(f"    {line}")
        return False

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls()
            if user and password:
                server.login(user, password)
            server.send_message(msg)
        log.info(f"Sent escalation email to {to_email}")
        return True
    except Exception as e:
        log.error(f"Failed to send email to {to_email}: {e}")
        return False


# ── Entry point ──────────────────────────────────────────────────────────────

def run() -> dict:
    gold_path = GOLD_DIR / "containers.json"
    if not gold_path.exists():
        log.warning("No Gold output found — run the pipeline before alerts")
        return {"emails_sent": 0, "containers_flagged": 0}

    gold_rows = json.loads(gold_path.read_text())
    owners_cfg = _load_owners()
    alert_state = _load_alert_state()
    dashboard_url = os.environ.get("DASHBOARD_URL", "")

    cleared = clear_resolved(gold_rows, alert_state)
    due_rows = find_escalations(gold_rows, alert_state)

    if not due_rows:
        _save_alert_state(alert_state)
        log.info(f"No new escalations due. ({cleared} resolved containers cleared from tracking.)")
        return {"emails_sent": 0, "containers_flagged": 0, "cleared": cleared}

    # Group by owner so each person gets one digest, not N separate emails
    grouped: dict = {}
    for row in due_rows:
        owner = _owner_for(row, owners_cfg)
        key = owner.get("email") or "UNROUTED"
        grouped.setdefault(key, {"owner": owner, "rows": []})
        grouped[key]["rows"].append(row)

    now_iso = datetime.now(timezone.utc).isoformat()
    emails_sent = 0

    for email_addr, bundle in grouped.items():
        owner = bundle["owner"]
        rows  = bundle["rows"]
        msg = _build_digest(owner.get("name", "Team"), rows, dashboard_url)
        sent = _send(msg, owner.get("email"))
        emails_sent += int(sent)

        for row in rows:
            cid = row.get("container_id")
            record = alert_state.get(cid, {"first_alert_at": now_iso, "escalation_count": 0})
            record["last_alert_at"] = now_iso
            record["escalation_count"] = record.get("escalation_count", 0) + 1
            record["owner_email"] = owner.get("email")
            alert_state[cid] = record

    _save_alert_state(alert_state)

    log.info(
        f"Escalations: {len(due_rows)} container(s) flagged across {len(grouped)} owner(s), "
        f"{emails_sent} email(s) sent, {cleared} resolved & cleared"
    )
    return {"emails_sent": emails_sent, "containers_flagged": len(due_rows), "cleared": cleared}


if __name__ == "__main__":
    run()
