"""
Throwaway live-SMTP check for services/mailer.py.

The unit tests drive the mailer against a fake `smtplib.SMTP`, which proves the call
ordering but not that a real socket conversation completes. This runs a minimal SMTP
listener in a thread, points the mailer at it, and asserts the DATA payload actually
arrived with the draft attached.

Not part of the pytest suite: it binds a port and is meant to be run by hand inside the
api container.

    docker compose exec -T api python scripts/smtp_live_check.py
"""

import asyncio
import socket
import sys
import threading
from pathlib import Path

# Run as a file, sys.path[0] is scripts/ rather than the app root, so `app` is not importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402

HOST = "127.0.0.1"
PORT = 2525

received: list[str] = []
_failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  {detail}" if detail else ""))


def serve_one() -> None:
    """Accept exactly one SMTP session and record the message body."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((HOST, PORT))
    listener.listen(1)
    conn, _ = listener.accept()
    conn.settimeout(10)
    f = conn.makefile("rwb")

    def reply(line: str) -> None:
        f.write(line.encode() + b"\r\n")
        f.flush()

    reply("220 localhost ESMTP throwaway")
    body: list[str] = []
    in_data = False
    while True:
        raw = f.readline()
        if not raw:
            break
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if in_data:
            if line == ".":
                in_data = False
                received.append("\n".join(body))
                reply("250 OK queued")
                continue
            body.append(line)
            continue
        verb = line.split(" ", 1)[0].upper()
        if verb == "EHLO":
            # No STARTTLS and no AUTH advertised: this listener speaks plaintext only.
            reply("250-localhost")
            reply("250 SIZE 10485760")
        elif verb == "HELO":
            reply("250 localhost")
        elif verb in ("MAIL", "RCPT"):
            reply("250 OK")
        elif verb == "DATA":
            in_data = True
            reply("354 End data with <CRLF>.<CRLF>")
        elif verb == "QUIT":
            reply("221 Bye")
            break
        else:
            reply("250 OK")
    f.close()
    conn.close()
    listener.close()


async def main() -> int:
    import os

    os.environ["SMTP_HOST"] = HOST
    os.environ["SMTP_PORT"] = str(PORT)
    os.environ["SMTP_USE_TLS"] = "false"
    os.environ["SMTP_USE_SSL"] = "false"
    os.environ["SMTP_USERNAME"] = ""
    os.environ["SMTP_PASSWORD"] = ""
    os.environ["SMTP_FROM"] = "agent@legalshield.test"
    get_settings.cache_clear()

    from app.services.mailer import send_counter_draft, smtp_configured

    check("live mode is active", smtp_configured() is True)

    server = threading.Thread(target=serve_one, daemon=True)
    server.start()
    await asyncio.sleep(0.3)

    draft = "PASAL 1 - PEMBAYARAN\nPembayaran dilakukan dalam 14 hari kerja."
    outcome = await send_counter_draft("contract-live-check", "klien@example.test", draft)
    server.join(timeout=10)

    check("mailer reports sent", outcome["status"] == "sent", str(outcome))
    check("the server received a message", len(received) == 1, f"count={len(received)}")

    if received:
        payload = received[0]
        check("recipient header present", "klien@example.test" in payload)
        check("sender header present", "agent@legalshield.test" in payload)
        check("contract id is traceable", "contract-live-check" in payload)
        check("draft text arrived", "PEMBAYARAN" in payload)
        check("attachment is present", "attachment" in payload.lower())

    get_settings.cache_clear()
    print()
    print("ALL CHECKS PASSED" if _failures == 0 else f"{_failures} CHECK(S) FAILED")
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
