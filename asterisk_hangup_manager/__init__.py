"""Asterisk hangup manager.

A small asyncio service that listens to Asterisk Manager Interface (AMI)
``Hangup`` events with Panoramisk, looks up a contact in a MySQL table
(``dst``, ``email``, ``description``) and sends a notification email.
"""

from .config import (
    AppConfig,
    AMIConfig,
    CdrConfig,
    MySQLConfig,
    SMTPConfig,
    load_config,
)
from .database import (
    CdrRepository,
    CdrSummary,
    HangupContact,
    HangupContactRepository,
)
from .mailer import EmailSender
from .service import HangupManager

__all__ = [
    "AppConfig",
    "AMIConfig",
    "CdrConfig",
    "MySQLConfig",
    "SMTPConfig",
    "load_config",
    "CdrRepository",
    "CdrSummary",
    "HangupContact",
    "HangupContactRepository",
    "EmailSender",
    "HangupManager",
]

__version__ = "0.1.0"
