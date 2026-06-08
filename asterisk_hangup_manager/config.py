"""Configuration loading for the Asterisk hangup manager.

Configuration is read from environment variables so that secrets (AMI
password, MySQL password, SMTP password) are never stored in the source
tree. See ``config.example.env`` for the full list of supported variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(ValueError):
    """Raised when the configuration is missing or invalid."""


def load_env_file(path: str | None = None) -> None:
    """Populate ``os.environ`` from a ``.env`` file if one is present.

    Lines are ``KEY=VALUE`` pairs; blank lines and ``#`` comments are
    ignored, an optional leading ``export`` is allowed and surrounding
    single or double quotes are stripped. Variables already present in the
    environment are **not** overwritten, so values provided by the shell or
    by systemd's ``EnvironmentFile=`` always take precedence. A missing file
    is silently ignored.

    The path defaults to ``$ENV_FILE`` when set, otherwise ``.env`` in the
    current working directory.
    """

    env_path = path or os.environ.get("ENV_FILE") or ".env"
    try:
        with open(env_path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except FileNotFoundError:
        return

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _get(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and (value is None or value == ""):
        raise ConfigError(f"Missing required environment variable: {name}")
    return value if value is not None else ""


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name} must be an integer") from exc


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AMIConfig:
    """Asterisk Manager Interface connection settings."""

    host: str = "127.0.0.1"
    port: int = 5038
    username: str = ""
    secret: str = ""
    # Name of the AMI Hangup event field used as the lookup key (``dst``).
    dst_field: str = "Exten"
    # AMI event that signals a dial/queue attempt created an outgoing channel.
    # While this event is seen for a call, the call is considered connected
    # (not missed). Asterisk emits ``DialBegin`` for both ``Dial()`` and queue
    # member attempts.
    dial_event: str = "DialBegin"


@dataclass(frozen=True)
class MySQLConfig:
    """MySQL connection settings and table/column mapping."""

    host: str = "127.0.0.1"
    port: int = 3306
    user: str = ""
    password: str = ""
    database: str = ""
    table: str = "hangup_contacts"
    dst_column: str = "dst"
    email_column: str = "email"
    description_column: str = "description"


@dataclass(frozen=True)
class SMTPConfig:
    """SMTP settings used to send notification emails."""

    host: str = "127.0.0.1"
    port: int = 25
    username: str = ""
    password: str = ""
    use_tls: bool = False
    start_tls: bool = False
    sender: str = "asterisk@localhost"
    subject_template: str = "Missed call to {dst}"
    # Predefined body for the missed-call notification. When empty a built-in
    # body is composed from the contact and call details. Supports the
    # ``{dst}``, ``{description}``, ``{channel}``, ``{caller_id}`` and
    # ``{cause}`` placeholders.
    body_template: str = ""
    # Recipient used when the matched contact has no email address of its own.
    fallback_email: str = ""


@dataclass(frozen=True)
class AppConfig:
    """Top-level application configuration."""

    ami: AMIConfig
    mysql: MySQLConfig
    smtp: SMTPConfig
    log_level: str = "INFO"


def load_config() -> AppConfig:
    """Build an :class:`AppConfig` from the current environment.

    A ``.env`` file (see :func:`load_env_file`) is loaded first when present,
    so the service can run without sourcing it into the shell beforehand.
    """

    load_env_file()

    ami = AMIConfig(
        host=_get("AMI_HOST", "127.0.0.1"),
        port=_get_int("AMI_PORT", 5038),
        username=_get("AMI_USERNAME", required=True),
        secret=_get("AMI_SECRET", required=True),
        dst_field=_get("AMI_DST_FIELD", "Exten"),
        dial_event=_get("AMI_DIAL_EVENT", "DialBegin"),
    )

    mysql = MySQLConfig(
        host=_get("MYSQL_HOST", "127.0.0.1"),
        port=_get_int("MYSQL_PORT", 3306),
        user=_get("MYSQL_USER", required=True),
        **{"password": _get("MYSQL_PASSWORD", "")},
        database=_get("MYSQL_DATABASE", required=True),
        table=_get("MYSQL_TABLE", "hangup_contacts"),
        dst_column=_get("MYSQL_DST_COLUMN", "dst"),
        email_column=_get("MYSQL_EMAIL_COLUMN", "email"),
        description_column=_get("MYSQL_DESCRIPTION_COLUMN", "description"),
    )

    smtp = SMTPConfig(
        host=_get("SMTP_HOST", "127.0.0.1"),
        port=_get_int("SMTP_PORT", 25),
        username=_get("SMTP_USERNAME", ""),
        **{"password": _get("SMTP_PASSWORD", "")},
        use_tls=_get_bool("SMTP_USE_TLS", False),
        start_tls=_get_bool("SMTP_START_TLS", False),
        sender=_get("SMTP_SENDER", "asterisk@localhost"),
        subject_template=_get("SMTP_SUBJECT_TEMPLATE", "Missed call to {dst}"),
        body_template=_get("SMTP_BODY_TEMPLATE", ""),
        fallback_email=_get("SMTP_FALLBACK_EMAIL", ""),
    )

    return AppConfig(
        ami=ami,
        mysql=mysql,
        smtp=smtp,
        log_level=_get("LOG_LEVEL", "INFO"),
    )
