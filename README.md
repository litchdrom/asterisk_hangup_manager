# Asterisk Hangup Manager

A small asyncio service that listens to Asterisk **Manager Interface (AMI)**
events with [Panoramisk](https://github.com/gawel/panoramisk), detects
**missed calls** (calls where no dial or queue attempt created an outgoing
channel), looks up a contact in a MySQL table (`dst`, `email`, `description`)
and sends a notification email.

## How it works

1. The service connects to Asterisk over AMI and subscribes to the dial
   event(s) (`AMI_DIAL_EVENT`, default `DialBegin,AgentCalled` — comma-separated
   and watched together) and to `Hangup` events. `Dial()` emits `DialBegin`,
   while queue member attempts are reported via `AgentCalled`.
2. While a dial/queue attempt creates an outgoing channel for a call, that
   call is remembered as *connected*.
3. When the call's originating channel hangs up, the service reads a lookup
   key from one or more configurable event fields (`AMI_DST_FIELD`, default
   `Exten,ConnectedLineNum`, tried in order; the special `h` hangup-handler
   extension is skipped).
4. If **no** channel was created for that call, it is treated as a *missed
   call*: the service queries the MySQL table for a row whose `dst` matches
   the key.
5. If a row is found, it emails the address in `email`, including the
   `description` and basic call details. When the row has no `email`, the
   notification is sent to the predefined `SMTP_FALLBACK_EMAIL` instead.

### CDR detection mode

The default `hangup` mode infers "missed" from live dial/queue events. When an
**IVR answers the caller** before a queue or dial attempt, the originating
channel's `Disposition` becomes `ANSWERED` even though no agent ever picked up,
which can hide genuinely missed calls. Set `AMI_DETECTION_MODE=cdr` (requires
Asterisk's `cdr_manager.conf` so the AMI `Cdr` event is emitted) to decide from
the CDR table instead:

1. The service subscribes to the AMI `Cdr` event (`AMI_CDR_EVENT`).
2. Only records whose `LastApplication` (`AMI_CDR_LASTAPP_FIELD`) is one of
   `AMI_CDR_APPS` (default `Dial,Queue`) are considered; calls that ended in an
   IVR are ignored.
3. The `Destination` field (`AMI_CDR_DST_FIELD`) is used as the `dst` key.
4. The CDR table is queried by the call's id (`AMI_CDR_LINKEDID_FIELD`, the
   originating channel's `UniqueID`, which equals the call's `linkedid`). The
   call counts as answered only when a **destination/agent channel** answered —
   a row whose disposition is `ANSWERED` *and* whose destination channel is not
   empty — so an IVR self-answer does not count.
5. When no destination channel answered, the call is treated as missed and the
   notification is sent as above.

The CDR table connection (`CDR_MYSQL_*`) defaults to the contact `MYSQL_*`
settings; override it when the CDR table lives in a separate database such as
`asteriskcdrdb`.

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
    email       VARCHAR(255) NULL,
    description VARCHAR(255) NOT NULL DEFAULT '',
    PRIMARY KEY (dst)
);
```

```sql
INSERT INTO hangup_contacts (dst, email, description) VALUES
    ('1001', 'reception@example.com', 'Front desk extension'),
    ('5555555', NULL, 'No mailbox: notify the fallback address');
```

Rows whose `email` is `NULL`/empty are still notified, but the email is sent
to `SMTP_FALLBACK_EMAIL` instead of the (missing) contact address.

The table and column names are configurable if your schema differs.

## Configuration

All settings come from environment variables so that passwords never live in
the repository. Copy [`config.example.env`](config.example.env) to `.env` and
edit it. The service loads `.env` automatically from the working directory (or
from the path in `ENV_FILE`), so you no longer need to source it into your
shell:

```bash
cp config.example.env .env
# edit .env, then just run the service
```

Values in `.env` override variables already present in the environment, so
editing `.env` always takes effect — even if a stale value was left exported
in your shell (for example from an earlier `set -a; source config.example.env`).
When running under systemd, the provided unit loads the same file via
`EnvironmentFile=`, so the two stay in sync.

| Variable | Default | Description |
| --- | --- | --- |
| `AMI_HOST` / `AMI_PORT` | `127.0.0.1` / `5038` | AMI address |
| `AMI_USERNAME` / `AMI_SECRET` | — (required) | AMI credentials |
| `AMI_DST_FIELD` | `Exten,ConnectedLineNum` | Hangup event field(s) used as the `dst` key, comma-separated and tried in order (`h` is skipped) |
| `AMI_DIAL_EVENT` | `DialBegin,AgentCalled` | Event(s) marking that a dial/queue created a channel (call connected), comma-separated |
| `AMI_DETECTION_MODE` | `hangup` | Missed-call detection mode: `hangup` (event tracking) or `cdr` (query the CDR table) |
| `AMI_CDR_EVENT` | `Cdr` | AMI event carrying a finalised CDR (cdr mode) |
| `AMI_CDR_DST_FIELD` | `Destination` | `Cdr` event field used as the `dst` key (cdr mode) |
| `AMI_CDR_LASTAPP_FIELD` | `LastApplication` | `Cdr` event field naming the last dialplan application (cdr mode) |
| `AMI_CDR_LINKEDID_FIELD` | `UniqueID` | `Cdr` event field tying the record to the call (cdr mode) |
| `AMI_CDR_APPS` | `Dial,Queue` | Applications whose CDR records count as a dial/queue attempt (cdr mode) |
| `MYSQL_HOST` / `MYSQL_PORT` | `127.0.0.1` / `3306` | MySQL address |
| `MYSQL_USER` / `MYSQL_PASSWORD` | — / empty | MySQL credentials (`MYSQL_USER` required) |
| `MYSQL_DATABASE` | — (required) | Database name |
| `MYSQL_TABLE` | `hangup_contacts` | Contact table |
| `MYSQL_DST_COLUMN` / `MYSQL_EMAIL_COLUMN` / `MYSQL_DESCRIPTION_COLUMN` | `dst` / `email` / `description` | Column mapping |
| `CDR_MYSQL_HOST` / `CDR_MYSQL_PORT` / `CDR_MYSQL_USER` / `CDR_MYSQL_PASSWORD` / `CDR_MYSQL_DATABASE` | MySQL `*` values | CDR DB connection (cdr mode); defaults to the contact MySQL settings |
| `CDR_TABLE` | `cdr` | CDR table (cdr mode) |
| `CDR_LINKEDID_COLUMN` / `CDR_DISPOSITION_COLUMN` / `CDR_DSTCHANNEL_COLUMN` | `linkedid` / `disposition` / `dstchannel` | CDR column mapping (cdr mode) |
| `CDR_ANSWERED_VALUE` | `ANSWERED` | Disposition value meaning a leg was answered (cdr mode) |
| `SMTP_HOST` / `SMTP_PORT` | `127.0.0.1` / `25` | SMTP address |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | empty | SMTP auth (optional) |
| `SMTP_USE_TLS` / `SMTP_START_TLS` | `false` | TLS / STARTTLS |
| `SMTP_VALIDATE_CERTS` | `true` | Set to `false` to skip TLS certificate verification (trusted/self-signed environments only) |
| `SMTP_SENDER` | `asterisk@localhost` | From address |
| `SMTP_SUBJECT_TEMPLATE` | `Missed call to {dst}` | Supports `{dst}`, `{description}`, `{channel}`, `{caller_id}`, `{cause}`, `{start_time}`, `{duration}` |
| `SMTP_BODY_TEMPLATE` | — (built-in body) | Optional body; supports `{dst}`, `{description}`, `{channel}`, `{caller_id}`, `{cause}`, `{start_time}`, `{duration}` |
| `SMTP_FALLBACK_EMAIL` | empty | Recipient used when the matched contact row has no email |
| `LOG_LEVEL` | `INFO` | Logging level |
| `ENV_FILE` | `.env` | Path to the env file loaded at startup |

## Running

```bash
python -m asterisk_hangup_manager
# or, after `pip install .`:
asterisk-hangup-manager
```

## Running as a systemd service

A ready-to-edit unit file is provided in
[`systemd/asterisk-hangup-manager.service`](systemd/asterisk-hangup-manager.service).
It uses `EnvironmentFile=` to load `.env` directly — no `set -a`/`source`
needed:

```bash
sudo cp systemd/asterisk-hangup-manager.service /etc/systemd/system/
# edit User, WorkingDirectory and EnvironmentFile paths to match your install
sudo systemctl daemon-reload
sudo systemctl enable --now asterisk-hangup-manager
```

## Testing

```bash
pip install -r requirements.txt pytest
pytest
```

The core logic is unit tested with in-memory fakes, so the tests do not need a
live Asterisk, MySQL or SMTP server.
