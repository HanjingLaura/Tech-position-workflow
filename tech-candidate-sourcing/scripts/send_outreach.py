#!/usr/bin/env python3
"""Dry-run or send approved personalized outreach emails from a queue CSV.

Default behavior is dry-run only. Real SMTP sending requires --send, approved
queue rows, and credentials from environment variables.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import smtplib
import ssl
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.headerregistry import Address
from email.utils import formatdate, make_msgid
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from candidate_schema import EMAIL_RE
from outreach_safety import has_unresolved_placeholder, has_unsafe_public_content


LOG_HEADERS = ["timestamp", "mode", "result", "to", "candidate_name", "subject", "reason"]
APPROVAL_METADATA_HEADERS = ["approved_at", "approved_by", "approval_note"]


def read_queue(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [{key: (value or "").strip() for key, value in row.items()} for row in reader]


def valid_email(value: str) -> bool:
    return bool(EMAIL_RE.fullmatch(value or ""))


def load_suppression_lists(paths: list[str]) -> set[str]:
    suppressed: set[str] = set()
    for value in paths:
        path = Path(value)
        if not path.exists():
            raise FileNotFoundError(f"Suppression list does not exist: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for email in EMAIL_RE.findall(line):
                suppressed.add(email.lower())
    return suppressed


def eligible_rows(
    rows: list[dict[str, str]],
    required_status: str,
    max_send: int,
    suppression: set[str] | None = None,
    require_approval_metadata: bool = True,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    suppression = suppression or set()
    eligible: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        email = row.get("to", "").lower()
        reason = ""
        if row.get("status", "").lower() != required_status.lower():
            reason = f"status is {row.get('status', '') or 'blank'}, expected {required_status}"
        elif not valid_email(row.get("to", "")):
            reason = "invalid or missing recipient email"
        elif not row.get("subject"):
            reason = "missing subject"
        elif not row.get("body"):
            reason = "missing body"
        elif has_unresolved_placeholder(row.get("subject", ""), row.get("body", "")):
            reason = "unresolved template placeholder in outbound copy"
        elif has_unsafe_public_content(row.get("subject", ""), row.get("body", "")):
            reason = "URL or instruction-like public-source content in outbound copy"
        elif require_approval_metadata and required_status.lower() == "approved" and any(
            not row.get(field) for field in APPROVAL_METADATA_HEADERS
        ):
            reason = "approved row missing approval audit metadata"
        elif email in suppression:
            reason = "recipient is on suppression list"
        elif email in seen:
            reason = "duplicate recipient"
        elif len(eligible) >= max_send:
            reason = "over --max-send limit"

        if reason:
            skipped.append({**row, "_reason": reason})
            continue

        seen.add(email)
        eligible.append(row)
    return eligible, skipped


def build_message(row: dict[str, str], from_email: str, from_name: str, reply_to: str) -> EmailMessage:
    message = EmailMessage()
    message["To"] = row["to"]
    message["From"] = Address(display_name=from_name, addr_spec=from_email) if from_name else Address(addr_spec=from_email)
    if reply_to:
        message["Reply-To"] = reply_to
    message["Subject"] = row["subject"]
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=from_email.rsplit("@", 1)[-1])
    message.set_content(row["body"])
    return message


def append_log(path: Path, entries: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LOG_HEADERS)
        if not exists:
            writer.writeheader()
        writer.writerows(entries)


def log_entry(mode: str, result: str, row: dict[str, str], reason: str = "") -> dict[str, str]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "result": result,
        "to": row.get("to", ""),
        "candidate_name": row.get("candidate_name", ""),
        "subject": row.get("subject", ""),
        "reason": reason,
    }


def require_send_config(args: argparse.Namespace) -> tuple[str, str, str, int, str, str]:
    from_email = args.from_email or os.getenv("SMTP_FROM", "")
    smtp_host = args.smtp_host or os.getenv("SMTP_HOST", "")
    smtp_user = args.smtp_user or os.getenv("SMTP_USER", "")
    smtp_password = os.getenv(args.smtp_password_env, "")
    if not from_email:
        raise ValueError("Missing sender. Pass --from-email or set SMTP_FROM.")
    if not smtp_host:
        raise ValueError("Missing SMTP host. Pass --smtp-host or set SMTP_HOST.")
    if not smtp_password:
        raise ValueError(f"Missing SMTP password. Set environment variable {args.smtp_password_env}.")
    return from_email, smtp_host, smtp_user, args.smtp_port, smtp_password, args.reply_to


def send_messages(args: argparse.Namespace, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    from_email, smtp_host, smtp_user, smtp_port, smtp_password, reply_to = require_send_config(args)
    sender_login = smtp_user or from_email
    entries: list[dict[str, str]] = []
    override_note = "explicit --i-know-what-im-doing override" if args.i_know_what_im_doing else ""
    context = ssl.create_default_context()
    security = args.smtp_security
    if security == "auto":
        security = "ssl" if smtp_port == 465 else "starttls"
    if security == "ssl":
        smtp_client = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30, context=context)
    else:
        smtp_client = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
    with smtp_client as smtp:
        if security == "starttls":
            smtp.starttls(context=context)
        smtp.login(sender_login, smtp_password)
        for row in rows:
            try:
                message = build_message(row, from_email, args.from_name, reply_to)
                smtp.send_message(message)
                entries.append(log_entry("send", "sent", row, override_note))
                if args.rate_seconds > 0:
                    time.sleep(args.rate_seconds)
            except Exception as exc:
                entries.append(log_entry("send", "error", row, str(exc)))
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run or send approved outreach emails from a queue CSV.")
    parser.add_argument("--queue", required=True, help="Queue CSV generated by draft_outreach.py --queue-csv.")
    parser.add_argument("--log", default="outputs/outreach_send_log.csv", help="CSV log path.")
    parser.add_argument("--status", default="approved", help="Only rows with this status are eligible. Default: approved.")
    parser.add_argument("--max-send", type=int, default=20, help="Maximum eligible rows to process.")
    parser.add_argument("--send", action="store_true", help="Actually send through SMTP. Omit for dry-run.")
    parser.add_argument("--i-know-what-im-doing", action="store_true", help="Explicit emergency override for non-approved status or missing approval metadata during --send. Use is recorded in the log reason.")
    parser.add_argument("--from-email", default="", help="Sender email. Defaults to SMTP_FROM.")
    parser.add_argument("--from-name", default="", help="Optional display name.")
    parser.add_argument("--reply-to", default="", help="Optional Reply-To address.")
    parser.add_argument("--smtp-host", default="", help="SMTP host. Defaults to SMTP_HOST.")
    parser.add_argument("--smtp-port", type=int, default=int(os.getenv("SMTP_PORT", "587")), help="SMTP port. Default: 587 or SMTP_PORT.")
    parser.add_argument("--smtp-user", default="", help="SMTP username. Defaults to SMTP_USER or sender email.")
    parser.add_argument("--smtp-password-env", default="SMTP_PASSWORD", help="Environment variable containing SMTP password.")
    parser.add_argument("--smtp-security", choices=["auto", "starttls", "ssl", "plain"], default="auto", help="SMTP transport. Auto uses SSL on 465 and STARTTLS otherwise.")
    parser.add_argument("--allow-insecure-plain-smtp", action="store_true", help="Explicitly allow plaintext SMTP AUTH. Unsafe; never use over untrusted networks.")
    parser.add_argument("--rate-seconds", type=float, default=2.0, help="Delay between sends when --send is used.")
    parser.add_argument("--suppression-list", action="append", default=[], help="Optional no-contact list, one email per line. Repeatable.")
    parser.add_argument("--no-require-approval-metadata", action="store_true", help="Allow approved rows without approved_at/approved_by/approval_note audit fields.")
    args = parser.parse_args()

    if args.max_send < 1:
        raise ValueError("--max-send must be at least 1.")
    if args.send and args.status.lower() != "approved" and not args.i_know_what_im_doing:
        raise ValueError("Real sending requires --status approved. Pass --i-know-what-im-doing only for an explicit emergency override.")
    if args.send and args.no_require_approval_metadata and not args.i_know_what_im_doing:
        raise ValueError("Real sending cannot disable approval metadata checks without --i-know-what-im-doing.")
    if args.send and args.smtp_security == "plain" and not args.allow_insecure_plain_smtp:
        raise ValueError("Plain SMTP would expose AUTH credentials. Pass --allow-insecure-plain-smtp only for an explicitly trusted transport.")

    rows = read_queue(Path(args.queue))
    eligible, skipped = eligible_rows(
        rows,
        args.status,
        args.max_send,
        load_suppression_lists(args.suppression_list),
        require_approval_metadata=not (args.no_require_approval_metadata and args.i_know_what_im_doing),
    )
    mode = "send" if args.send else "dry-run"

    entries: list[dict[str, str]] = []
    entries.extend(log_entry(mode, "skipped", row, row.get("_reason", "")) for row in skipped)
    send_error = ""
    try:
        if args.send:
            entries.extend(send_messages(args, eligible))
        else:
            entries.extend(log_entry(mode, "eligible", row) for row in eligible)
    except Exception as exc:
        send_error = f"SMTP setup/login failed: {type(exc).__name__}: {exc}"
        entries.extend(log_entry(mode, "error", row, send_error) for row in eligible)
    finally:
        append_log(Path(args.log), entries)
    print(f"Queue rows: {len(rows)}")
    print(f"Eligible ({args.status}): {len(eligible)}")
    print(f"Skipped: {len(skipped)}")
    print(f"Mode: {mode}")
    print(f"Log: {args.log}")
    if not args.send:
        print("Dry-run only. No email was sent.")
    if send_error:
        print(send_error, file=sys.stderr)
        return 4
    if args.send and any(entry.get("result") == "error" for entry in entries):
        print("One or more recipients failed; see the send log.", file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
