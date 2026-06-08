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
    """Detect missed calls from AMI events and email the matching contact.

    A call is treated as *missed* when its originating channel hangs up
    without any dial or queue attempt having created an outgoing channel
    (signalled by the configured :attr:`AMIConfig.dial_event`, ``DialBegin``
    by default). Such calls are looked up by ``dst`` and a notification email
    is sent to the contact's address, falling back to a predefined recipient
    when the contact has no email of its own.

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
        # Call ids (Linkedid/Uniqueid) for which a dial/queue attempt has
        # already created an outgoing channel. Such calls are not "missed".
        self._connected_calls: set[str] = set()

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

    @staticmethod
    def call_id(event: Mapping[str, Any]) -> str | None:
        """Return the identifier that ties the legs of a call together."""

        value = event.get("Linkedid") or event.get("Uniqueid")
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @staticmethod
    def is_originating_leg(event: Mapping[str, Any]) -> bool:
        """Return ``True`` when the event belongs to the call's first channel.

        Destination channels created by a dial/queue attempt share the call's
        ``Linkedid`` but carry their own ``Uniqueid``; only the originating
        channel has ``Uniqueid == Linkedid``. When the correlation ids are
        absent (older Asterisk) the hangup is treated as the call itself.
        """

        linkedid = event.get("Linkedid")
        uniqueid = event.get("Uniqueid")
        if linkedid and uniqueid:
            return str(linkedid) == str(uniqueid)
        return True

    async def handle_dial(self, event: Mapping[str, Any]) -> None:
        """Record that a dial/queue attempt created a channel for the call."""

        call_id = self.call_id(event)
        if call_id is not None:
            self._connected_calls.add(call_id)

    def build_email_body(
        self, contact: HangupContact, event: Mapping[str, Any]
    ) -> str:
        """Compose the notification email body.

        When :attr:`SMTPConfig.body_template` is set it is used verbatim
        (with placeholders); otherwise a built-in body is composed from the
        contact and call details.
        """

        channel = event.get("Channel", "unknown")
        caller_id = event.get("CallerIDNum", "unknown")
        cause = event.get("Cause-txt") or event.get("Cause", "unknown")
        if self._config.smtp.body_template:
            return self._config.smtp.body_template.format(
                dst=contact.dst,
                description=contact.description,
                channel=channel,
                caller_id=caller_id,
                cause=cause,
            )
        lines = [f"A missed call was detected for {contact.dst}.", ""]
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

        Returns ``True`` when a missed-call notification email was sent.
        """

        # Only the originating channel ends the call; ignore the hangups of
        # destination channels created by a dial/queue attempt.
        if not self.is_originating_leg(event):
            return False

        call_id = self.call_id(event)
        # A channel was created for this call when the dial event was seen.
        channel_created = call_id is not None and call_id in self._connected_calls
        if call_id is not None:
            self._connected_calls.discard(call_id)

        dst = self.extract_dst(event, self._config.ami.dst_field)
        if dst is None:
            logger.debug(
                "Hangup event without '%s' field; ignoring",
                self._config.ami.dst_field,
            )
            return False

        if channel_created:
            logger.debug("Call to dst=%s was connected; not a missed call", dst)
            return False

        try:
            contact = await self._repository.get_contact(dst)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Failed to look up contact for dst=%s", dst)
            return False

        if contact is None:
            logger.debug("No contact registered for dst=%s; ignoring", dst)
            return False

        recipient = contact.email or self._config.smtp.fallback_email
        if not recipient:
            logger.warning(
                "Missed call for dst=%s has no contact email and no "
                "SMTP_FALLBACK_EMAIL is configured; cannot notify",
                dst,
            )
            return False

        subject = self._config.smtp.subject_template.format(
            dst=contact.dst, description=contact.description
        )
        body = self.build_email_body(contact, event)

        try:
            await self._mailer.send(recipient, subject, body)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception(
                "Failed to send missed-call email to %s for dst=%s", recipient, dst
            )
            return False

        logger.info(
            "Sent missed-call notification for dst=%s to %s", dst, recipient
        )
        return True

    async def _on_hangup(self, _manager: Manager, message: Mapping[str, Any]) -> None:
        await self.handle_hangup(message)

    async def _on_dial(self, _manager: Manager, message: Mapping[str, Any]) -> None:
        await self.handle_dial(message)

    async def run(self) -> None:
        """Connect to AMI and process call events until cancelled."""

        await self._repository.connect()
        manager = self._manager or self._build_manager()
        self._manager = manager
        manager.register_event(self._config.ami.dial_event, self._on_dial)
        manager.register_event("Hangup", self._on_hangup)

        try:
            await manager.connect()
            logger.info(
                "Connected to AMI at %s:%s; listening for missed calls",
                self._config.ami.host,
                self._config.ami.port,
            )
            # Block forever; panoramisk runs callbacks on the event loop.
            await asyncio.Event().wait()
        finally:
            manager.close()
            await self._repository.close()
