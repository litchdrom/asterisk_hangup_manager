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


def _config(dst_field="Exten"):
    return AppConfig(
        ami=AMIConfig(username="u", secret="s", dst_field=dst_field),
        mysql=MySQLConfig(user="u", database="d"),
        smtp=SMTPConfig(subject_template="Hangup on {dst}"),
    )


def _manager(contacts, mailer, dst_field="Exten"):
    return HangupManager(_config(dst_field), FakeRepository(contacts), mailer)


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
