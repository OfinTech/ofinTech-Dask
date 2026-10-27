"""Outgoing e-mail (invitations, password resets) over SMTP. Optional: without DESK_SMTP_HOST the app shows links
for the administrator to pass on instead."""

from __future__ import annotations

import asyncio
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Any

log = logging.getLogger("desk.mail")


class Mailer:
    def __init__(self, host: str = "", port: int = 587, user: str = "", password: str = "", sender: str = "",
                 security: str = "starttls") -> None:
        self.host, self.port, self.user, self.password = host, port, user, password
        self.sender = sender or (f"OfinTech Desk <{user}>" if "@" in user else "")
        self.security = security
        self.sent: list[EmailMessage] = []  # kept for tests and the audit trail of this process

    @classmethod
    def from_env(cls) -> Mailer:
        def env(name: str, default: str = "") -> str:
            path = os.environ.get(name + "_FILE", "").strip()
            if path:
                return Path(path).read_text().strip()
            return os.environ.get(name, default).strip()

        return cls(host=env("DESK_SMTP_HOST"), port=int(env("DESK_SMTP_PORT", "587") or 587), user=env("DESK_SMTP_USER"),
                   password=env("DESK_SMTP_PASSWORD"), sender=env("DESK_SMTP_FROM"),
                   security=(env("DESK_SMTP_SECURITY", "starttls") or "starttls").lower())

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender)

    def _send_sync(self, msg: EmailMessage) -> None:
        ctx = ssl.create_default_context()
        if self.security == "ssl":
            server: Any = smtplib.SMTP_SSL(self.host, self.port, context=ctx, timeout=30)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=30)
        try:
            if self.security == "starttls":
                server.starttls(context=ctx)
            if self.user:
                server.login(self.user, self.password)
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:  # pragma: no cover
                pass

    async def send(self, to: str, subject: str, text: str) -> bool:
        """True when the message was handed to the SMTP server."""
        if not self.configured:
            return False
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = formataddr(("", to))
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid(domain=self.sender.rsplit("@", 1)[-1].strip(">") or None)
        msg.set_content(text)
        try:
            await asyncio.to_thread(self._send_sync, msg)
        except Exception as exc:
            log.warning("could not send e-mail to %s: %s", to, type(exc).__name__)
            return False
        self.sent.append(msg)
        return True


def invite_text(inviter: str, workspace: str, link: str) -> tuple[str, str]:
    return (f"{inviter} invited you to {workspace} on OfinTech Desk",
            f"{inviter} invited you to the workspace “{workspace}” on OfinTech Desk.\n\n"
            f"Accept the invitation (valid for 7 days, works once):\n{link}\n\n"
            "If you did not expect this e-mail, you can ignore it.\n")


def reset_text(link: str) -> tuple[str, str]:
    return ("Reset your OfinTech Desk password",
            "Someone (hopefully you) asked to reset the password of your OfinTech Desk account.\n\n"
            f"Choose a new password (the link is valid for one hour and works once):\n{link}\n\n"
            "If you did not ask for this, ignore this e-mail; your password stays the same.\n")


def added_text(inviter: str, workspace: str, link: str) -> tuple[str, str]:
    return (f"You were added to {workspace} on OfinTech Desk",
            f"{inviter} added you to the workspace “{workspace}” on OfinTech Desk.\n\nOpen it: {link}\n")
