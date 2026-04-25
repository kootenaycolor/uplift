"""Gmail SMTP helper for drive-uploader."""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def send(
    sender_email: str,
    app_password: str,
    recipient: str,
    subject: str,
    body: str,
) -> None:
    """Send email via Gmail SMTP using an App Password.

    Raises smtplib.SMTPException (or subclass) on failure.
    """
    msg = MIMEMultipart("alternative")
    msg["From"] = sender_email
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(sender_email, app_password)
        server.sendmail(sender_email, recipient, msg.as_string())
