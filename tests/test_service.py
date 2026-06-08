import asyncio

from asterisk_hangup_manager.config import (
    AMIConfig,
    AppConfig,
    CdrConfig,
    MySQLConfig,
    SMTPConfig,
)
from asterisk_hangup_manager.database import CdrSummary, HangupContact
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


class FakeCdrRepository:
    """Return canned :class:`CdrSummary` values keyed by linkedid."""

    def __init__(self, summaries):
        self._summaries = summaries
        self.connected = False
        self.closed = False
        self.queried = []

    async def connect(self):
        self.connected = True

    async def close(self):
        self.closed = True

    async def summarize(self, linkedid):
        self.queried.append(linkedid)
        return self._summaries.get(linkedid, CdrSummary(total=0, answered=0))


def _config(
    dst_field="Exten",
    fallback_email="",
    body_template="",
    ami=None,
):
    return AppConfig(
        ami=ami or AMIConfig(username="u", secret="s", dst_field=dst_field),
        mysql=MySQLConfig(user="u", database="d"),
        cdr=CdrConfig(user="u", database="d"),
        smtp=SMTPConfig(
            subject_template="Hangup on {dst}",
            fallback_email=fallback_email,
            body_template=body_template,
        ),
    )


def _manager(
    contacts,
    mailer,
    dst_field="Exten",
    fallback_email="",
    body_template="",
    ami=None,
    cdr_repository=None,
):
    return HangupManager(
        _config(dst_field, fallback_email, body_template, ami=ami),
        FakeRepository(contacts),
        mailer,
        cdr_repository=cdr_repository,
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
        cdr=config.cdr,
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


def _cdr_ami(**overrides):
    params = dict(
        username="u",
        secret="s",
        detection_mode="cdr",
        cdr_event="Cdr",
        cdr_dst_field="Destination",
        cdr_lastapp_field="LastApplication",
        cdr_linkedid_field="UniqueID",
        cdr_apps="Dial,Queue",
    )
    params.update(overrides)
    return AMIConfig(**params)


def _cdr_event(**overrides):
    event = {
        "Destination": "78126",
        "LastApplication": "Queue",
        "UniqueID": "call-1",
        "Source": "8911",
        "Channel": "SIP/sev_poo-00071f21",
        "StartTime": "2026-06-08 22:15:34",
        "Duration": "2",
    }
    event.update(overrides)
    return event


def test_handle_cdr_no_answered_destination_sends_email():
    contacts = {"78126": HangupContact("78126", "did@example.com", "DID")}
    mailer = FakeMailer()
    cdr = FakeCdrRepository({"call-1": CdrSummary(total=3, answered=0)})
    manager = _manager(contacts, mailer, ami=_cdr_ami(), cdr_repository=cdr)

    result = asyncio.run(manager.handle_cdr(_cdr_event()))

    assert result is True
    assert mailer.sent[0][0] == "did@example.com"
    assert cdr.queried == ["call-1"]


def test_handle_cdr_answered_destination_is_not_missed():
    contacts = {"78126": HangupContact("78126", "did@example.com", "DID")}
    mailer = FakeMailer()
    # An IVR self-answer plus an answered agent leg.
    cdr = FakeCdrRepository({"call-1": CdrSummary(total=3, answered=1)})
    manager = _manager(contacts, mailer, ami=_cdr_ami(), cdr_repository=cdr)

    result = asyncio.run(manager.handle_cdr(_cdr_event()))

    assert result is False
    assert mailer.sent == []


def test_handle_cdr_ignores_non_dial_queue_application():
    contacts = {"78126": HangupContact("78126", "did@example.com", "DID")}
    mailer = FakeMailer()
    cdr = FakeCdrRepository({"call-1": CdrSummary(total=1, answered=0)})
    manager = _manager(contacts, mailer, ami=_cdr_ami(), cdr_repository=cdr)

    result = asyncio.run(
        manager.handle_cdr(_cdr_event(LastApplication="Read"))
    )

    assert result is False
    assert mailer.sent == []
    # The CDR table is not even queried for ignored applications.
    assert cdr.queried == []


def test_handle_cdr_ignores_sub_leg_without_cdr_rows():
    contacts = {"78126": HangupContact("78126", "did@example.com", "DID")}
    mailer = FakeMailer()
    # A destination leg whose UniqueID is not the call's linkedid: no rows.
    cdr = FakeCdrRepository({})
    manager = _manager(contacts, mailer, ami=_cdr_ami(), cdr_repository=cdr)

    result = asyncio.run(
        manager.handle_cdr(_cdr_event(UniqueID="dest-1"))
    )

    assert result is False
    assert mailer.sent == []


def test_handle_cdr_deduplicates_repeated_events():
    contacts = {"78126": HangupContact("78126", "did@example.com", "DID")}
    mailer = FakeMailer()
    cdr = FakeCdrRepository({"call-1": CdrSummary(total=2, answered=0)})
    manager = _manager(contacts, mailer, ami=_cdr_ami(), cdr_repository=cdr)

    first = asyncio.run(manager.handle_cdr(_cdr_event()))
    second = asyncio.run(manager.handle_cdr(_cdr_event()))

    assert first is True
    assert second is False
    assert len(mailer.sent) == 1


def test_handle_cdr_body_includes_start_time_and_duration():
    contacts = {"78126": HangupContact("78126", "did@example.com", "DID")}
    mailer = FakeMailer()
    cdr = FakeCdrRepository({"call-1": CdrSummary(total=1, answered=0)})
    manager = _manager(contacts, mailer, ami=_cdr_ami(), cdr_repository=cdr)

    asyncio.run(manager.handle_cdr(_cdr_event()))

    body = mailer.sent[0][2]
    assert "Start time: 2026-06-08 22:15:34" in body
    assert "Duration: 2" in body


def test_run_cdr_mode_registers_cdr_event():
    fake = FakeManager()
    config = _config(ami=_cdr_ami())
    cdr = FakeCdrRepository({})
    manager = HangupManager(
        config, FakeRepository({}), FakeMailer(), fake, cdr_repository=cdr
    )

    async def _run_and_cancel():
        task = asyncio.ensure_future(manager.run())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run_and_cancel())

    assert fake.registered == ["Cdr"]
    assert cdr.connected is True


def test_on_hangup_swallows_handler_exceptions():
    mailer = FakeMailer()
    manager = _manager({}, mailer)

    async def _boom(_event):
        raise RuntimeError("boom")

    manager.handle_hangup = _boom

    # The wrapper must not propagate; panoramisk schedules this coroutine
    # with ensure_future and never retrieves its result, so an escaping
    # exception would surface as "Task exception was never retrieved".
    asyncio.run(manager._on_hangup(None, {"Exten": "1001"}))


def test_on_dial_swallows_handler_exceptions():
    manager = _manager({}, FakeMailer())

    async def _boom(_event):
        raise RuntimeError("boom")

    manager.handle_dial = _boom

    asyncio.run(manager._on_dial(None, {"Linkedid": "1"}))


def test_on_cdr_swallows_handler_exceptions():
    manager = _manager({}, FakeMailer())

    async def _boom(_event):
        raise RuntimeError("boom")

    manager.handle_cdr = _boom

    asyncio.run(manager._on_cdr(None, {"Cdr": "x"}))
