import email
import logging
from email.utils import parsedate_to_datetime

logger = logging.getLogger(__name__)


def parse_eml(file_bytes: bytes) -> dict:
    """
    Parse a raw .eml file into a dict matching our Artifact fields.
    """
    try:
        msg = email.message_from_bytes(file_bytes)
    except Exception as e:
        logger.error(f"parse_eml: failed to parse email: {e}")
        return {}

    subject = msg.get("Subject", "")
    sender = msg.get("From", "")
    recipient = msg.get("To", "")
    date_raw = msg.get("Date", "")
    message_id = msg.get("Message-ID", "")

    received_at = None
    if date_raw:
        try:
            received_at = parsedate_to_datetime(date_raw)
        except Exception:
            pass

    text_content = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/plain" and "attachment" not in disposition:
                try:
                    text_content = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                    break
                except Exception:
                    pass
    else:
        try:
            text_content = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="replace"
            )
        except Exception:
            pass

    return {
        "subject": subject,
        "sender": sender,
        "recipient": recipient,
        "text_content": text_content,
        "received_at": received_at,
        "metadata": {
            "from": sender,
            "to": recipient,
            "message_id": message_id,
            "date": date_raw,
        },
    }
