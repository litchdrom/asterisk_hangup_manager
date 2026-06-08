from asterisk_hangup_manager.config import SMTPConfig
from asterisk_hangup_manager.mailer import EmailSender


def test_build_message():
    sender = EmailSender(SMTPConfig(sender="noreply@example.com"))
    message = sender.build_message("to@example.com", "Subject", "Body text")

    assert message["From"] == "noreply@example.com"
    assert message["To"] == "to@example.com"
    assert message["Subject"] == "Subject"
    assert message.get_content().strip() == "Body text"
