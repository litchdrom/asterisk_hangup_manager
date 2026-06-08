"""MySQL access layer for hangup contact lookups."""

from __future__ import annotations

import re
from dataclasses import dataclass

import aiomysql

from .config import CdrConfig, MySQLConfig

# Identifiers (table/column names) come from configuration, not from event
# data, but we still validate them so a typo cannot produce a malformed or
# unsafe query. Values (the ``dst`` lookup key) are always passed as bound
# parameters and never interpolated into the SQL string.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_identifier(name: str) -> str:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return f"`{name}`"


@dataclass(frozen=True)
class HangupContact:
    """A contact to notify when a missed call is detected.

    ``email`` may be empty when the row exists but has no address; in that
    case the caller is expected to fall back to a predefined recipient.
    """

    dst: str
    email: str
    description: str


class HangupContactRepository:
    """Look up :class:`HangupContact` rows in MySQL using an aiomysql pool."""

    def __init__(self, config: MySQLConfig) -> None:
        self._config = config
        self._pool: aiomysql.Pool | None = None
        self._query = self._build_query(config)

    @staticmethod
    def _build_query(config: MySQLConfig) -> str:
        table = _safe_identifier(config.table)
        dst_col = _safe_identifier(config.dst_column)
        email_col = _safe_identifier(config.email_column)
        desc_col = _safe_identifier(config.description_column)
        return (
            f"SELECT {dst_col} AS dst, {email_col} AS email, "
            f"{desc_col} AS description FROM {table} WHERE {dst_col} = %s LIMIT 1"
        )

    async def connect(self) -> None:
        """Create the underlying connection pool."""

        if self._pool is not None:
            return
        self._pool = await aiomysql.create_pool(
            host=self._config.host,
            port=self._config.port,
            user=self._config.user,
            **{"password": self._config.password},
            db=self._config.database,
            autocommit=True,
        )

    async def close(self) -> None:
        """Close the connection pool and wait for it to drain."""

        if self._pool is None:
            return
        self._pool.close()
        await self._pool.wait_closed()
        self._pool = None

    async def get_contact(self, dst: str) -> HangupContact | None:
        """Return the contact registered for ``dst`` or ``None`` if absent."""

        if self._pool is None:
            raise RuntimeError("Repository is not connected; call connect() first")

        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(self._query, (dst,))
                row = await cursor.fetchone()

        if not row:
            return None
        return HangupContact(
            dst=str(row["dst"]),
            email=row["email"] or "",
            description=row["description"] or "",
        )

    async def __aenter__(self) -> "HangupContactRepository":
        await self.connect()
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()


@dataclass(frozen=True)
class CdrSummary:
    """Aggregated CDR information for a single call (one ``linkedid``)."""

    total: int
    answered: int

    @property
    def has_record(self) -> bool:
        """Whether any CDR row exists for the call."""

        return self.total > 0

    @property
    def is_answered(self) -> bool:
        """Whether a destination/agent channel answered the call."""

        return self.answered > 0


class CdrRepository:
    """Query the Asterisk CDR table to decide whether a call was answered."""

    def __init__(self, config: CdrConfig) -> None:
        self._config = config
        self._pool: aiomysql.Pool | None = None
        self._query = self._build_query(config)

    @staticmethod
    def _build_query(config: CdrConfig) -> str:
        table = _safe_identifier(config.table)
        linkedid = _safe_identifier(config.linkedid_column)
        disposition = _safe_identifier(config.disposition_column)
        dstchannel = _safe_identifier(config.dstchannel_column)
        # Count every leg of the call, and separately the legs that reached an
        # answered destination channel. A non-empty destination channel is what
        # tells a real agent/dial leg apart from the originating channel's own
        # record, so an IVR answering the caller is not mistaken for the call
        # being answered.
        return (
            f"SELECT COUNT(*) AS total, "
            f"SUM({disposition} = %s AND {dstchannel} <> '') AS answered "
            f"FROM {table} WHERE {linkedid} = %s"
        )

    async def connect(self) -> None:
        """Create the underlying connection pool."""

        if self._pool is not None:
            return
        self._pool = await aiomysql.create_pool(
            host=self._config.host,
            port=self._config.port,
            user=self._config.user,
            **{"password": self._config.password},
            db=self._config.database,
            autocommit=True,
        )

    async def close(self) -> None:
        """Close the connection pool and wait for it to drain."""

        if self._pool is None:
            return
        self._pool.close()
        await self._pool.wait_closed()
        self._pool = None

    async def summarize(self, linkedid: str) -> CdrSummary:
        """Return the CDR summary for the call identified by ``linkedid``."""

        if self._pool is None:
            raise RuntimeError("Repository is not connected; call connect() first")

        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    self._query, (self._config.answered_value, linkedid)
                )
                row = await cursor.fetchone()

        if not row:
            return CdrSummary(total=0, answered=0)
        return CdrSummary(
            total=int(row["total"] or 0),
            answered=int(row["answered"] or 0),
        )

    async def __aenter__(self) -> "CdrRepository":
        await self.connect()
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()
