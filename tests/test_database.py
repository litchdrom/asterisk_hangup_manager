import pytest

from asterisk_hangup_manager.config import MySQLConfig
from asterisk_hangup_manager.database import HangupContactRepository, _safe_identifier


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
