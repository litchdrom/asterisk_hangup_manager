import asyncio

from asterisk_hangup_manager.config import (
    AMIConfig,
    AppConfig,
    MySQLConfig,
    SMTPConfig,
)
from asterisk_hangup_manager.database import HangupContact
from asterisk_hangup_manager.service import HangupManager


class FakeRepository:
    def __init__(self, contacts):
        self._contacts = contacts
        self.connected = False
        self.closed = False

    async def connect(self):
        self.connected = True

    async def close(self):
        self.closed = True

    async def get_contact(self, dst):
        return self._contacts.get(dst)


class FakeMailer:
    def __init__(self):
        self.sent = []

    async def send(self, to, subject, body):
        self.sent.append((to, subject, body))


class BrokenMailer:
    async def send(self, to, subject, body):
        raise RuntimeError("smtp down")


def _config(dst_field="Exten", fallback_email="", body_template=""):
    return AppConfig(
        ami=AMIConfig(username="u", secret="s", dst_field=dst_field),
        mysql=MySQLConfig(user="u", database="d"),
        smtp=SMTPConfig(
            subject_template="Hangup on {dst}",
            fallback_email=fallback_email,
            body_template=body_template,
        ),
    )


def _manager(contacts, mailer, dst_field="Exten", fallback_email="", body_template=""):
    return HangupManager(
        _config(dst_field, fallback_email, body_template),
        FakeRepository(contacts),
        mailer,
    )


def test_handle_hangup_sends_email():
    contacts = {
        "1001": HangupContact("1001", "reception@example.com", "Front desk"),
    }
    mailer = FakeMailer()
    manager = _manager(contacts, mailer)

    event = {"Exten": "1001", "Channel": "SIP/1001-0001", "CallerIDNum": "555"}
    result = asyncio.run(manager.handle_hangup(event))

    assert result is True
    assert len(mailer.sent) == 1
    to, subject, body = mailer.sent[0]
    assert to == "reception@example.com"
    assert subject == "Hangup on 1001"
    assert "Front desk" in body
    assert "SIP/1001-0001" in body


def test_handle_hangup_unknown_dst_does_not_send():
    mailer = FakeMailer()
    manager = _manager({}, mailer)

    result = asyncio.run(manager.handle_hangup({"Exten": "9999"}))

    assert result is False
    assert mailer.sent == []


def test_handle_hangup_missing_field_does_not_send():
    mailer = FakeMailer()
    manager = _manager({"1001": HangupContact("1001", "a@b.c", "x")}, mailer)

    result = asyncio.run(manager.handle_hangup({"Channel": "SIP/1001-0001"}))

    assert result is False
    assert mailer.sent == []


def test_handle_hangup_custom_dst_field():
    contacts = {"555": HangupContact("555", "caller@example.com", "VIP")}
    mailer = FakeMailer()
    manager = _manager(contacts, mailer, dst_field="CallerIDNum")

    result = asyncio.run(manager.handle_hangup({"CallerIDNum": "555"}))

    assert result is True
    assert mailer.sent[0][0] == "caller@example.com"


def test_handle_hangup_mailer_failure_returns_false():
    contacts = {"1001": HangupContact("1001", "a@b.c", "x")}
    manager = _manager(contacts, BrokenMailer())

    result = asyncio.run(manager.handle_hangup({"Exten": "1001"}))

    assert result is False


def test_extract_dst_strips_whitespace():
    assert HangupManager.extract_dst({"Exten": "  1001 "}, "Exten") == "1001"
    assert HangupManager.extract_dst({"Exten": "   "}, "Exten") is None
    assert HangupManager.extract_dst({}, "Exten") is None


def test_contact_without_email_uses_fallback():
    contacts = {"5555555": HangupContact("5555555", "", "No mailbox")}
    mailer = FakeMailer()
    manager = _manager(contacts, mailer, fallback_email="missed@example.com")

    result = asyncio.run(manager.handle_hangup({"Exten": "5555555"}))

    assert result is True
    assert mailer.sent[0][0] == "missed@example.com"


def test_contact_without_email_and_no_fallback_does_not_send():
    contacts = {"5555555": HangupContact("5555555", "", "No mailbox")}
    mailer = FakeMailer()
    manager = _manager(contacts, mailer)

    result = asyncio.run(manager.handle_hangup({"Exten": "5555555"}))

    assert result is False
    assert mailer.sent == []


def test_connected_call_is_not_missed():
    contacts = {"1001": HangupContact("1001", "a@b.c", "x")}
    mailer = FakeMailer()
    manager = _manager(contacts, mailer)

    # A dial attempt created an outgoing channel for the call.
    asyncio.run(manager.handle_dial({"Linkedid": "call-1", "Uniqueid": "call-1"}))
    result = asyncio.run(
        manager.handle_hangup(
            {"Exten": "1001", "Linkedid": "call-1", "Uniqueid": "call-1"}
        )
    )

    assert result is False
    assert mailer.sent == []


def test_call_without_channel_is_missed():
    contacts = {"1001": HangupContact("1001", "a@b.c", "x")}
    mailer = FakeMailer()
    manager = _manager(contacts, mailer)

    result = asyncio.run(
        manager.handle_hangup(
            {"Exten": "1001", "Linkedid": "call-1", "Uniqueid": "call-1"}
        )
    )

    assert result is True
    assert mailer.sent[0][0] == "a@b.c"


def test_destination_leg_hangup_is_ignored():
    contacts = {"1001": HangupContact("1001", "a@b.c", "x")}
    mailer = FakeMailer()
    manager = _manager(contacts, mailer)

    # Destination channel: shares Linkedid but has its own Uniqueid.
    result = asyncio.run(
        manager.handle_hangup(
            {"Exten": "1001", "Linkedid": "call-1", "Uniqueid": "dest-1"}
        )
    )

    assert result is False
    assert mailer.sent == []


def test_body_template_is_used_when_set():
    contacts = {"1001": HangupContact("1001", "a@b.c", "Front desk")}
    mailer = FakeMailer()
    manager = _manager(
        contacts, mailer, body_template="Missed {dst} ({description})"
    )

    result = asyncio.run(manager.handle_hangup({"Exten": "1001"}))

    assert result is True
    assert mailer.sent[0][2] == "Missed 1001 (Front desk)"
