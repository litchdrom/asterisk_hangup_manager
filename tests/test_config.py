import pytest

from asterisk_hangup_manager.config import ConfigError, load_config


REQUIRED = {
    "AMI_USERNAME": "user",
    "AMI_SECRET": "secret",
    "MYSQL_USER": "dbuser",
    "MYSQL_DATABASE": "asterisk",
}


def _set_required(monkeypatch):
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)


def test_load_config_defaults(monkeypatch):
    for key in list(REQUIRED):
        monkeypatch.delenv(key, raising=False)
    _set_required(monkeypatch)

    config = load_config()

    assert config.ami.host == "127.0.0.1"
    assert config.ami.port == 5038
    assert config.ami.username == "user"
    assert config.ami.dst_field == "Exten"
    assert config.mysql.table == "hangup_contacts"
    assert config.smtp.port == 25
    assert config.smtp.use_tls is False


def test_load_config_overrides(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("AMI_PORT", "5040")
    monkeypatch.setenv("SMTP_USE_TLS", "yes")
    monkeypatch.setenv("AMI_DST_FIELD", "CallerIDNum")

    config = load_config()

    assert config.ami.port == 5040
    assert config.ami.dst_field == "CallerIDNum"
    assert config.smtp.use_tls is True


def test_load_config_missing_required(monkeypatch):
    for key in REQUIRED:
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ConfigError):
        load_config()


def test_load_config_invalid_int(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("AMI_PORT", "not-a-number")

    with pytest.raises(ConfigError):
        load_config()
