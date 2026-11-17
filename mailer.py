import logging
import smtplib
import ssl
from email.message import EmailMessage


logger = logging.getLogger(__name__)


class Mailer:
    def __init__(self, config):
        self.host = config["SMTP_HOST"]
        self.port = config["SMTP_PORT"]
        self.username = config["SMTP_USER"]
        self.password = config["SMTP_PASSWORD"].replace(" ", "")
        self.sender = config["MAIL_FROM"]

    @property
    def configured(self):
        gmail_requires_login = self.host.lower() in {
            "smtp.gmail.com",
        }
        return bool(
            self.host
            and self.sender
            and (
                not gmail_requires_login
                or (self.username and self.password)
            )
        )

    def send(self, recipient, subject, body):
        if not self.configured:
            return False
        try:
            message = EmailMessage()
            message["Subject"] = subject
            message["From"] = self.sender
            message["To"] = recipient
            message.set_content(body)
            with smtplib.SMTP(self.host, self.port, timeout=10) as server:
                server.starttls(context=ssl.create_default_context())
                if self.username:
                    server.login(self.username, self.password)
                server.send_message(message)
            return True
        except (OSError, smtplib.SMTPException, ValueError):
            logger.exception("Could not send email to %s", recipient)
            return False
