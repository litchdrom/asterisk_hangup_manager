import pytest

from asterisk_hangup_manager.config import CdrConfig, MySQLConfig
from asterisk_hangup_manager.database import (
    CdrRepository,
    CdrSummary,
    HangupContactRepository,
    _safe_identifier,
)


def test_safe_identifier_accepts_valid():
    assert _safe_identifier("hangup_contacts") == "`hangup_contacts`"


@pytest.mark.parametrize("bad", ["bad name", "drop;table", "col-1", "", "1col"])
def test_safe_identifier_rejects_invalid(bad):
    with pytest.raises(ValueError):
        _safe_identifier(bad)


def test_build_query_uses_configured_columns():
    config = MySQLConfig(
        table="contacts",
        dst_column="destination",
        email_column="mail",
        description_column="descr",
    )
    query = HangupContactRepository._build_query(config)

    assert "`contacts`" in query
    assert "`destination`" in query
    assert "`mail` AS email" in query
    assert "`descr` AS description" in query
    # The lookup value must be a bound parameter, never interpolated.
    assert "WHERE `destination` = %s" in query


def test_build_query_rejects_unsafe_table():
    config = MySQLConfig(table="contacts; DROP TABLE users")
    with pytest.raises(ValueError):
        HangupContactRepository._build_query(config)


def test_cdr_build_query_uses_configured_columns():
    config = CdrConfig(
        table="cdr_records",
        linkedid_column="linked",
        disposition_column="dispo",
        dstchannel_column="dchan",
    )
    query = CdrRepository._build_query(config)

    assert "`cdr_records`" in query
    assert "WHERE `linked` = %s" in query
    assert "`dispo` = %s" in query
    assert "`dchan` <> ''" in query
    assert "COUNT(*) AS total" in query
    assert "AS answered" in query


def test_cdr_build_query_rejects_unsafe_table():
    config = CdrConfig(table="cdr; DROP TABLE users")
    with pytest.raises(ValueError):
        CdrRepository._build_query(config)


def test_cdr_summary_properties():
    assert CdrSummary(total=0, answered=0).has_record is False
    assert CdrSummary(total=2, answered=0).has_record is True
    assert CdrSummary(total=2, answered=0).is_answered is False
    assert CdrSummary(total=2, answered=1).is_answered is True
