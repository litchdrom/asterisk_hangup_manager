import pytest

from asterisk_hangup_manager.config import ConfigError, load_config, load_env_file


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
    assert config.ami.dial_event == "DialBegin"
    assert config.smtp.subject_template == "Missed call to {dst}"
    assert config.smtp.fallback_email == ""
    assert config.smtp.body_template == ""


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


def test_load_env_file_sets_missing_keys(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "AMI_USERNAME=fromfile",
                'AMI_SECRET="quoted secret"',
                "export MYSQL_USER=exported",
                "MYSQL_DATABASE=asterisk",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    for key in ("AMI_USERNAME", "AMI_SECRET", "MYSQL_USER", "MYSQL_DATABASE"):
        monkeypatch.delenv(key, raising=False)

    load_env_file(str(env_file))

    import os

    assert os.environ["AMI_USERNAME"] == "fromfile"
    assert os.environ["AMI_SECRET"] == "quoted secret"
    assert os.environ["MYSQL_USER"] == "exported"
    assert os.environ["MYSQL_DATABASE"] == "asterisk"


def test_load_env_file_does_not_override_existing(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("AMI_USERNAME=fromfile\n", encoding="utf-8")
    monkeypatch.setenv("AMI_USERNAME", "fromenv")

    load_env_file(str(env_file))

    import os

    assert os.environ["AMI_USERNAME"] == "fromenv"


def test_load_env_file_missing_is_ignored(tmp_path):
    # Should not raise when the file does not exist.
    load_env_file(str(tmp_path / "does-not-exist.env"))


def test_load_config_reads_env_file(monkeypatch, tmp_path):
    for key in list(REQUIRED):
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / "custom.env"
    env_file.write_text(
        "\n".join(
            [
                "AMI_USERNAME=user",
                "AMI_SECRET=secret",
                "MYSQL_USER=dbuser",
                "MYSQL_DATABASE=asterisk",
                "AMI_PORT=5060",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ENV_FILE", str(env_file))

    config = load_config()

    assert config.ami.username == "user"
    assert config.ami.port == 5060
