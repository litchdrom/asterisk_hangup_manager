"""Panoramisk-based service that reacts to Asterisk hangup events."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping

from panoramisk import Manager

from .config import AppConfig
from .database import HangupContact, HangupContactRepository
from .mailer import EmailSender

logger = logging.getLogger(__name__)


class HangupManager:
    """Listen for AMI ``Hangup`` events and email the matching contact.

    The class is split so the core reaction logic (:meth:`handle_hangup`)
    can be unit tested without a live Asterisk, MySQL or SMTP server.
    """

    def __init__(
        self,
        config: AppConfig,
        repository: HangupContactRepository,
        mailer: EmailSender,
        manager: Manager | None = None,
    ) -> None:
        self._config = config
        self._repository = repository
        self._mailer = mailer
        self._manager = manager

    def _build_manager(self) -> Manager:
        ami = self._config.ami
        return Manager(
            host=ami.host,
            port=ami.port,
            username=ami.username,
            secret=ami.secret,
        )

    @staticmethod
    def extract_dst(event: Mapping[str, Any], field: str) -> str | None:
        """Return the lookup key from a Hangup event, or ``None``."""

        value = event.get(field)
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    def build_email_body(
        self, contact: HangupContact, event: Mapping[str, Any]
    ) -> str:
        """Compose the notification email body."""

        channel = event.get("Channel", "unknown")
        caller_id = event.get("CallerIDNum", "unknown")
        cause = event.get("Cause-txt") or event.get("Cause", "unknown")
        lines = [f"A hangup was detected for {contact.dst}.", ""]
        if contact.description:
            lines.append(f"Description: {contact.description}")
        lines.extend(
            [
                f"Channel: {channel}",
                f"Caller ID: {caller_id}",
                f"Hangup cause: {cause}",
            ]
        )
        return "\n".join(lines)

    async def handle_hangup(self, event: Mapping[str, Any]) -> bool:
        """Process a single Hangup event.

        Returns ``True`` when a notification email was sent.
        """

        dst = self.extract_dst(event, self._config.ami.dst_field)
        if dst is None:
            logger.debug(
                "Hangup event without '%s' field; ignoring",
                self._config.ami.dst_field,
            )
            return False

        try:
            contact = await self._repository.get_contact(dst)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Failed to look up contact for dst=%s", dst)
            return False

        if contact is None:
            logger.debug("No contact registered for dst=%s; ignoring", dst)
            return False

        subject = self._config.smtp.subject_template.format(
            dst=contact.dst, description=contact.description
        )
        body = self.build_email_body(contact, event)

        try:
            await self._mailer.send(contact.email, subject, body)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception(
                "Failed to send hangup email to %s for dst=%s", contact.email, dst
            )
            return False

        logger.info("Sent hangup notification for dst=%s to %s", dst, contact.email)
        return True

    async def _on_hangup(self, _manager: Manager, message: Mapping[str, Any]) -> None:
        await self.handle_hangup(message)

    async def run(self) -> None:
        """Connect to AMI and process hangup events until cancelled."""

        await self._repository.connect()
        manager = self._manager or self._build_manager()
        self._manager = manager
        manager.register_event("Hangup", self._on_hangup)

        try:
            await manager.connect()
            logger.info(
                "Connected to AMI at %s:%s; listening for Hangup events",
                self._config.ami.host,
                self._config.ami.port,
            )
            # Block forever; panoramisk runs callbacks on the event loop.
            await asyncio.Event().wait()
        finally:
            manager.close()
            await self._repository.close()
