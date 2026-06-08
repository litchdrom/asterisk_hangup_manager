"""Command-line entry point for the Asterisk hangup manager."""

from __future__ import annotations

import asyncio
import logging

from .config import AppConfig, ConfigError, load_config
from .database import CdrRepository, HangupContactRepository
from .mailer import EmailSender
from .service import HangupManager

logger = logging.getLogger("asterisk_hangup_manager")


async def _run(config: AppConfig) -> None:
    repository = HangupContactRepository(config.mysql)
    mailer = EmailSender(config.smtp)
    cdr_repository = (
        CdrRepository(config.cdr)
        if config.ami.detection_mode.strip().lower() == "cdr"
        else None
    )
    service = HangupManager(
        config, repository, mailer, cdr_repository=cdr_repository
    )
    await service.run()


def main() -> int:
    """Load configuration and run the service. Returns a process exit code."""

    try:
        config = load_config()
    except ConfigError as exc:
        logging.basicConfig(level=logging.INFO)
        logger.error("Configuration error: %s", exc)
        return 2

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        logger.info("Shutting down on interrupt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
