"""Gmail SMTP helper for drive-uploader."""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def _addr_list(raw: str) -> list[str]:
    """Parse a comma-separated address string into a cleaned list."""
    return [a.strip() for a in raw.split(",") if a.strip()]


def send(
    sender_email: str,
    app_password: str,
    recipient: str,
    subject: str,
    body: str,
    cc: str = "",
    bcc: str = "",
) -> None:
    """Send email via Gmail SMTP using an App Password.

    recipient, cc, bcc may each be comma-separated address strings.
    Raises smtplib.SMTPException (or subclass) on failure.
    """
    to_list  = _addr_list(recipient)
    cc_list  = _addr_list(cc)
    bcc_list = _addr_list(bcc)
    all_rcpt = to_list + cc_list + bcc_list

    msg = MIMEMultipart("alternative")
    msg["From"]    = sender_email
    msg["To"]      = ", ".join(to_list)
    msg["Subject"] = subject
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    # BCC is intentionally not added as a header — SMTP envelope handles delivery
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(sender_email, app_password)
        server.sendmail(sender_email, all_rcpt, msg.as_string())
