# Asterisk Hangup Manager

A small asyncio service that listens to Asterisk **Manager Interface (AMI)**
`Hangup` events with [Panoramisk](https://github.com/gawel/panoramisk), looks
up a contact in a MySQL table (`dst`, `email`, `description`) and sends a
notification email.

## How it works

1. The service connects to Asterisk over AMI and subscribes to `Hangup` events.
2. For each hangup it reads a lookup key from a configurable event field
   (`AMI_DST_FIELD`, default `Exten`).
3. It queries the MySQL table for a row whose `dst` matches that key.
4. If a row is found, it emails the address in `email`, including the
   `description` and basic call details.

## Requirements

- Python 3.10+
- An Asterisk server with a configured AMI user (see `manager.conf`)
- A MySQL/MariaDB database
- An SMTP server to relay mail

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# or: pip install .
```

## Database setup

Create the contact table (see [`schema.sql`](schema.sql)):

```sql
CREATE TABLE IF NOT EXISTS hangup_contacts (
    dst         VARCHAR(64)  NOT NULL,
    email       VARCHAR(255) NOT NULL,
    description VARCHAR(255) NOT NULL DEFAULT '',
    PRIMARY KEY (dst)
);
```

```sql
INSERT INTO hangup_contacts (dst, email, description) VALUES
    ('1001', 'reception@example.com', 'Front desk extension');
```

The table and column names are configurable if your schema differs.

## Configuration

All settings come from environment variables so that passwords never live in
the repository. Copy [`config.example.env`](config.example.env) to `.env`,
edit it, then load it before running:

```bash
cp config.example.env .env
# edit .env
set -a && source .env && set +a
```

| Variable | Default | Description |
| --- | --- | --- |
| `AMI_HOST` / `AMI_PORT` | `127.0.0.1` / `5038` | AMI address |
| `AMI_USERNAME` / `AMI_SECRET` | — (required) | AMI credentials |
| `AMI_DST_FIELD` | `Exten` | Hangup event field used as the `dst` key |
| `MYSQL_HOST` / `MYSQL_PORT` | `127.0.0.1` / `3306` | MySQL address |
| `MYSQL_USER` / `MYSQL_PASSWORD` | — / empty | MySQL credentials (`MYSQL_USER` required) |
| `MYSQL_DATABASE` | — (required) | Database name |
| `MYSQL_TABLE` | `hangup_contacts` | Contact table |
| `MYSQL_DST_COLUMN` / `MYSQL_EMAIL_COLUMN` / `MYSQL_DESCRIPTION_COLUMN` | `dst` / `email` / `description` | Column mapping |
| `SMTP_HOST` / `SMTP_PORT` | `127.0.0.1` / `25` | SMTP address |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | empty | SMTP auth (optional) |
| `SMTP_USE_TLS` / `SMTP_START_TLS` | `false` | TLS / STARTTLS |
| `SMTP_SENDER` | `asterisk@localhost` | From address |
| `SMTP_SUBJECT_TEMPLATE` | `Hangup detected on {dst}` | Supports `{dst}` and `{description}` |
| `LOG_LEVEL` | `INFO` | Logging level |

## Running

```bash
python -m asterisk_hangup_manager
# or, after `pip install .`:
asterisk-hangup-manager
```

## Testing

```bash
pip install -r requirements.txt pytest
pytest
```

The core logic is unit tested with in-memory fakes, so the tests do not need a
live Asterisk, MySQL or SMTP server.
