"""Panoramisk-based service that reacts to Asterisk hangup events."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any, Mapping

from panoramisk import Manager

from .config import AppConfig, ConfigError
from .database import CdrRepository, HangupContact, HangupContactRepository
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
        cdr_repository: CdrRepository | None = None,
    ) -> None:
        self._config = config
        self._repository = repository
        self._mailer = mailer
        self._manager = manager
        self._cdr_repository = cdr_repository
        # Call ids (Linkedid/Uniqueid) for which a dial/queue attempt has
        # already created an outgoing channel. Such calls are not "missed".
        self._connected_calls: set[str] = set()
        # Bounded history of call ids already notified in ``cdr`` mode, so a
        # repeated Cdr event for the same call does not email twice.
        self._notified_calls: set[str] = set()
        self._notified_order: deque[str] = deque()
        self._notified_max = 1024

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
        """Return the lookup key from a Hangup event, or ``None``.

        ``field`` may name a single AMI field or a comma-separated list of
        fields that are tried in order until one yields a usable value. This
        lets the ``Exten`` field (which does not always carry the dialled DID,
        and is reported as the special ``h`` hangup-handler extension when the
        call traversed one) fall back to, for example, ``ConnectedLineNum``.

        The special ``h`` extension is skipped because it is Asterisk's
        hangup-handler extension rather than a real destination number.
        """

        for name in field.split(","):
            name = name.strip()
            if not name:
                continue
            value = event.get(name)
            if value is None:
                continue
            value = str(value).strip()
            if not value or value == "h":
                continue
            return value
        return None

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
        caller_id = (
            event.get("CallerIDNum") or event.get("Source") or "unknown"
        )
        cause = event.get("Cause-txt") or event.get("Cause", "unknown")
        start_time = event.get("StartTime", "unknown")
        duration = event.get("Duration", "unknown")
        if self._config.smtp.body_template:
            return self._config.smtp.body_template.format(
                dst=contact.dst,
                description=contact.description,
                channel=channel,
                caller_id=caller_id,
                cause=cause,
                start_time=start_time,
                duration=duration,
            )
        lines = [f"A missed call was detected for {contact.dst}.", ""]
        if contact.description:
            lines.append(f"Description: {contact.description}")
        lines.extend(
            [
                f"Channel: {channel}",
                f"Caller ID: {caller_id}",
                f"Hangup cause: {cause}",
                f"Start time: {start_time}",
                f"Duration: {duration}",
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

        return await self._notify_missed(dst, event)

    async def _notify_missed(
        self, dst: str, event: Mapping[str, Any]
    ) -> bool:
        """Look up the contact for ``dst`` and email a missed-call notice.

        Returns ``True`` when a notification email was sent.
        """

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

    async def handle_cdr(self, event: Mapping[str, Any]) -> bool:
        """Process a single AMI ``Cdr`` event (``cdr`` detection mode).

        The originating channel's ``Disposition`` cannot be trusted: an IVR
        that answers the caller before a queue/dial attempt marks the call
        ``ANSWERED`` even when no agent ever picks up. Instead the CDR table is
        queried for every leg of the call and the call is only treated as
        answered when a destination/agent channel answered.

        Returns ``True`` when a missed-call notification email was sent.
        """

        if self._cdr_repository is None:  # pragma: no cover - defensive
            logger.error("Cdr event received but no CDR repository is configured")
            return False

        ami = self._config.ami

        # Only records that reached a dial/queue attempt are of interest;
        # calls that ended in an IVR (for example a ``Read``) are ignored.
        last_app = event.get(ami.cdr_lastapp_field)
        apps = {a.strip() for a in ami.cdr_apps.split(",") if a.strip()}
        if apps and (last_app is None or str(last_app).strip() not in apps):
            logger.debug(
                "Cdr event with %s=%r not in %s; ignoring",
                ami.cdr_lastapp_field,
                last_app,
                sorted(apps),
            )
            return False

        dst = self.extract_dst(event, ami.cdr_dst_field)
        if dst is None:
            logger.debug(
                "Cdr event without '%s' field; ignoring", ami.cdr_dst_field
            )
            return False

        linkedid = event.get(ami.cdr_linkedid_field)
        linkedid = str(linkedid).strip() if linkedid is not None else ""
        if not linkedid:
            logger.debug(
                "Cdr event without '%s' field; ignoring", ami.cdr_linkedid_field
            )
            return False

        try:
            summary = await self._cdr_repository.summarize(linkedid)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Failed to query CDR for linkedid=%s", linkedid)
            return False

        # No rows means this record is a sub-leg (its unique id is not the
        # call's linkedid) or the row is not written yet; either way there is
        # nothing to decide on here.
        if not summary.has_record:
            logger.debug(
                "No CDR rows for linkedid=%s; not the originating leg", linkedid
            )
            return False

        if summary.is_answered:
            logger.debug(
                "Call to dst=%s (linkedid=%s) had an answered destination "
                "channel; not a missed call",
                dst,
                linkedid,
            )
            return False

        # Avoid emailing twice for the same call if Asterisk emits more than
        # one Cdr event for the originating channel.
        if linkedid in self._notified_calls:
            logger.debug(
                "Already notified for linkedid=%s; skipping", linkedid
            )
            return False
        self._remember_notified(linkedid)

        return await self._notify_missed(dst, event)

    def _remember_notified(self, linkedid: str) -> None:
        """Record ``linkedid`` as notified, bounding the in-memory history."""

        self._notified_calls.add(linkedid)
        self._notified_order.append(linkedid)
        while len(self._notified_order) > self._notified_max:
            oldest = self._notified_order.popleft()
            self._notified_calls.discard(oldest)

    async def _on_hangup(self, _manager: Manager, message: Mapping[str, Any]) -> None:
        await self.handle_hangup(message)

    async def _on_dial(self, _manager: Manager, message: Mapping[str, Any]) -> None:
        await self.handle_dial(message)

    async def _on_cdr(self, _manager: Manager, message: Mapping[str, Any]) -> None:
        await self.handle_cdr(message)

    async def run(self) -> None:
        """Connect to AMI and process call events until cancelled."""

        await self._repository.connect()
        manager = self._manager or self._build_manager()
        self._manager = manager

        cdr_mode = self._config.ami.detection_mode.strip().lower() == "cdr"
        if cdr_mode:
            if self._cdr_repository is None:
                raise ConfigError(
                    "AMI_DETECTION_MODE=cdr requires a CDR repository"
                )
            await self._cdr_repository.connect()
            manager.register_event(self._config.ami.cdr_event, self._on_cdr)
        else:
            for dial_event in self._config.ami.dial_event.split(","):
                dial_event = dial_event.strip()
                if dial_event:
                    manager.register_event(dial_event, self._on_dial)
            manager.register_event("Hangup", self._on_hangup)

        try:
            await manager.connect()
            logger.info(
                "Connected to AMI at %s:%s; listening for missed calls (%s mode)",
                self._config.ami.host,
                self._config.ami.port,
                "cdr" if cdr_mode else "hangup",
            )
            # Block forever; panoramisk runs callbacks on the event loop.
            await asyncio.Event().wait()
        finally:
            manager.close()
            await self._repository.close()
            if self._cdr_repository is not None:
                await self._cdr_repository.close()
