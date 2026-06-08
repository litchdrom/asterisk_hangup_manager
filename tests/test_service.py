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


def test_extract_dst_falls_back_to_next_field():
    fields = "Exten,ConnectedLineNum"
    # `Exten` is the hangup-handler extension; fall back to ConnectedLineNum.
    event = {"Exten": "h", "ConnectedLineNum": "2001"}
    assert HangupManager.extract_dst(event, fields) == "2001"
    # Empty/missing leading field also falls through.
    assert HangupManager.extract_dst({"ConnectedLineNum": "2001"}, fields) == "2001"
    # `Exten` is preferred when it holds a real value.
    assert (
        HangupManager.extract_dst(
            {"Exten": "1001", "ConnectedLineNum": "2001"}, fields
        )
        == "1001"
    )
    # No usable field yields None.
    assert HangupManager.extract_dst({"Exten": "h"}, fields) is None


def test_handle_hangup_uses_connected_line_num_when_exten_is_h():
    contacts = {"2001": HangupContact("2001", "did@example.com", "DID")}
    mailer = FakeMailer()
    manager = _manager(contacts, mailer, dst_field="Exten,ConnectedLineNum")

    result = asyncio.run(
        manager.handle_hangup({"Exten": "h", "ConnectedLineNum": "2001"})
    )

    assert result is True
    assert mailer.sent[0][0] == "did@example.com"



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


class FakeManager:
    def __init__(self):
        self.registered = []
        self.connected = False
        self.closed = False

    def register_event(self, name, callback):
        self.registered.append(name)

    async def connect(self):
        self.connected = True

    def close(self):
        self.closed = True


def test_run_registers_each_configured_dial_event(monkeypatch):
    fake = FakeManager()
    config = _config()
    config = AppConfig(
        ami=AMIConfig(
            username="u",
            secret="s",
            dial_event="DialBegin,AgentCalled",
        ),
        mysql=config.mysql,
        smtp=config.smtp,
    )
    manager = HangupManager(config, FakeRepository({}), FakeMailer(), fake)

    async def _run_and_cancel():
        task = asyncio.ensure_future(manager.run())
        # Let run() register events and reach the blocking wait.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run_and_cancel())

    assert fake.registered == ["DialBegin", "AgentCalled", "Hangup"]
