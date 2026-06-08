"""Asynchronous email delivery using aiosmtplib."""

from __future__ import annotations

from email.message import EmailMessage

import aiosmtplib

from .config import SMTPConfig


class EmailSender:
    """Send notification emails over SMTP."""

    def __init__(self, config: SMTPConfig) -> None:
        self._config = config

    def build_message(self, to: str, subject: str, body: str) -> EmailMessage:
        """Build an :class:`~email.message.EmailMessage`."""

        message = EmailMessage()
        message["From"] = self._config.sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        return message

    async def send(self, to: str, subject: str, body: str) -> None:
        """Deliver an email to ``to``."""

        message = self.build_message(to, subject, body)
        await aiosmtplib.send(
            message,
            hostname=self._config.host,
            port=self._config.port,
            username=self._config.username or None,
            **{"password": self._config.password or None},
            use_tls=self._config.use_tls,
            start_tls=self._config.start_tls,
        )
