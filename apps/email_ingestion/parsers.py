import email
import logging
import re
from email.utils import parsedate_to_datetime
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Thread split patterns ────────────────────────────────────────────────────

THREAD_SPLITTERS = [
    # Outlook: "From: X\nSent: Y\nTo: Z\nSubject: W"
    re.compile(
        r"(?:^|\n)[ \t]*From:[ \t]*(.+?)\n[ \t]*Sent:[ \t]*(.+?)\n[ \t]*To:[ \t]*(.+?)\n[ \t]*Subject:[ \t]*(.+?)(?=\n)",
        re.IGNORECASE,
    ),
    # Gmail: "On Mon, Mar 9, 2026 at 3:49 PM Name <email> wrote:"
    re.compile(
        r"(?:^|\n)On\s+.{5,80}wrote:\s*\n",
        re.IGNORECASE,
    ),
    # Apple Mail forwarded
    re.compile(
        r"(?:^|\n)[ \t]*Begin forwarded message:[ \t]*\n",
        re.IGNORECASE,
    ),
    # Generic forwarded/original message divider
    re.compile(
        r"(?:^|\n)[ \t]*-{4,}[ \t]*(?:Forwarded|Original)[ \t]+[Mm]essage[ \t]*-{4,}[ \t]*\n",
        re.IGNORECASE,
    ),
]


# ─── HTML stripping ───────────────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    """Strip HTML tags and clean up whitespace."""
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<(br|p|div|tr|li)[^>]*>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    html = (html.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#39;", "'"))
    html = re.sub(r"<image\d+\.\w+>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


def _clean_text(text: str) -> str:
    """
    Aggressively remove corporate email noise:
    - Legal disclaimers
    - Email signatures (name, title, phone, address, website)
    - Image placeholders
    - Repeated boilerplate
    """
    # Remove image placeholders like <image001.png>
    text = re.sub(r"<image\d+\.\w+>", "", text, flags=re.IGNORECASE)

    # Remove legal disclaimer blocks
    text = re.sub(
        r"LEGAL DISCLAIMER.{0,3000}?(prohibited\.|immediately\.)",
        "", text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(
        r"This message is solely for the use.{0,1500}?(such\.|immediately\.)",
        "", text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(
        r"The contents of this electronic communication.{0,1500}?(prohibited\.|immediately\.)",
        "", text, flags=re.DOTALL | re.IGNORECASE
    )

    # Remove "View Privacy Policy" and similar footer links
    text = re.sub(r"View\s+Privacy\s+Policy", "", text, flags=re.IGNORECASE)
    text = re.sub(r"View\s+my\s+profile", "", text, flags=re.IGNORECASE)

    # Remove corporate boilerplate
    text = re.sub(r"Proud to be.{0,300}", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"Headquartered in.{0,300}", "", text, flags=re.DOTALL | re.IGNORECASE)

    # Remove signature blocks after closing salutations
    # Captures everything from "Kind regards/Sincerely/Best" through
    # the signature (name, title, address, phone, web) up to a blank line
    text = re.sub(
        r"(Kind regards|Sincerely|Best regards|Regards|Thank you),?\s*\n"
        r"(?:[ \t]*\n)*"  # optional blank lines
        r"(?:[ \t]*.+\n){0,8}"  # up to 8 lines of signature content
        r"(?:[ \t]*(?:d:|w:|t:|f:|Direct:|Main:|Mobile:).+\n?){0,5}",  # contact lines
        r"\1,\n",
        text, flags=re.IGNORECASE
    )

    # Remove standalone address lines
    text = re.sub(
        r"^\s*\d+\s+\w[\w\s]+(?:Avenue|Street|Road|Drive|Blvd|Way|Lane|St\b|Ave\b).*$",
        "", text, flags=re.IGNORECASE | re.MULTILINE
    )

    # Remove city/postal code lines like "Toronto, ON M4L 1A4"
    text = re.sub(
        r"^\s*[A-Za-z\s]+,\s*[A-Z]{2}\s+[A-Z]\d[A-Z]\s*\d[A-Z]\d\s*$",
        "", text, flags=re.MULTILINE
    )

    # Remove lines that are purely phone numbers
    text = re.sub(r"^\s*[\+\d\s\-\(\)\.]{7,25}\s*$", "", text, flags=re.MULTILINE)

    # Remove lines that are purely URLs
    text = re.sub(r"^\s*https?://\S+\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*www\.\S+\s*$", "", text, flags=re.MULTILINE)

    # Remove lines that are just "d: 647-905-6838" or "w: www.britacan.com"
    text = re.sub(r"^\s*[dwt]:\s*[\S]+\s*$", "", text, flags=re.MULTILINE | re.IGNORECASE)

    # Remove lines with just image references
    text = re.sub(r"^\s*\[image\d+\]\s*$", "", text, flags=re.MULTILINE | re.IGNORECASE)

    # Remove zero-width spaces and other unicode garbage
    text = re.sub(r"[\u200b\u200c\u200d\ufeff\u00ad]", "", text)

    # Collapse excessive whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# ─── Body extraction ──────────────────────────────────────────────────────────

def _extract_body(msg) -> str:
    """Extract plain text body, falling back to cleaned HTML."""
    text_content = ""

    if msg.is_multipart():
        # Prefer plain text
        for part in msg.walk():
            ct = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in disp:
                try:
                    text_content = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                    break
                except Exception:
                    pass

        # Fall back to HTML
        if not text_content:
            for part in msg.walk():
                ct = part.get_content_type()
                disp = str(part.get("Content-Disposition", ""))
                if ct == "text/html" and "attachment" not in disp:
                    try:
                        html = part.get_payload(decode=True).decode(
                            part.get_content_charset() or "utf-8", errors="replace"
                        )
                        text_content = _strip_html(html)
                        break
                    except Exception:
                        pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                text_content = payload.decode(
                    msg.get_content_charset() or "utf-8", errors="replace"
                )
                if msg.get_content_type() == "text/html":
                    text_content = _strip_html(text_content)
        except Exception:
            pass

    return _clean_text(text_content.strip())


# ─── Thread detection and splitting ──────────────────────────────────────────

def _is_thread(text: str) -> bool:
    """Check if body contains a forwarded/replied thread."""
    for pattern in THREAD_SPLITTERS:
        if pattern.search(text):
            return True
    return False


def _parse_thread_block(block: str, fallback_subject: str = "") -> Optional[dict]:
    """Parse a single thread block into a message dict."""
    block = block.strip()
    if not block:
        return None

    sender = ""
    date_raw = ""
    subject = fallback_subject
    received_at = None

    from_match = re.search(r"^From:\s*(.+)$", block, re.IGNORECASE | re.MULTILINE)
    sent_match = re.search(r"^Sent:\s*(.+)$", block, re.IGNORECASE | re.MULTILINE)
    date_match = re.search(r"^Date:\s*(.+)$", block, re.IGNORECASE | re.MULTILINE)
    subj_match = re.search(r"^Subject:\s*(.+)$", block, re.IGNORECASE | re.MULTILINE)

    if from_match:
        sender = from_match.group(1).strip()
    if subj_match:
        subject = subj_match.group(1).strip()

    raw_date = sent_match or date_match
    if raw_date:
        date_raw = raw_date.group(1).strip()
        try:
            received_at = parsedate_to_datetime(date_raw)
        except Exception:
            pass

    # Strip quoted header lines from body
    body = re.sub(r"^(From|Sent|To|Cc|Date|Subject):.*$", "", block, flags=re.IGNORECASE | re.MULTILINE)
    body = _clean_text(body)

    if not body or len(body) < 15:
        return None

    return {
        "subject": subject,
        "sender": sender,
        "recipient": "",
        "text_content": body,
        "received_at": received_at,
        "metadata": {
            "from": sender,
            "to": "",
            "message_id": "",
            "date": date_raw if isinstance(date_raw, str) else "",
        },
    }


def _split_thread(text: str, fallback_subject: str = "") -> list[dict]:
    """Split a thread body into individual message dicts, oldest first."""
    split_points = [0]
    for pattern in THREAD_SPLITTERS:
        for match in pattern.finditer(text):
            split_points.append(match.start())

    split_points = sorted(set(split_points))
    split_points.append(len(text))

    blocks = []
    for i in range(len(split_points) - 1):
        block = text[split_points[i]:split_points[i + 1]]
        parsed = _parse_thread_block(block, fallback_subject)
        if parsed:
            blocks.append(parsed)

    blocks.reverse()
    return blocks


# ─── Main entry point ─────────────────────────────────────────────────────────

def parse_eml(file_bytes: bytes) -> list[dict]:
    """
    Parse a raw .eml file into a list of message dicts.

    Returns:
    - Single email → list with one dict
    - Forwarded thread → list with one dict per message (oldest first)

    Each dict: subject, sender, recipient, text_content, received_at, metadata
    """
    try:
        msg = email.message_from_bytes(file_bytes)
    except Exception as e:
        logger.error(f"parse_eml: failed to parse email: {e}")
        return []

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

    text_content = _extract_body(msg)

    if text_content and _is_thread(text_content):
        logger.info(f"parse_eml: detected thread in '{subject}', splitting")
        messages = _split_thread(text_content, fallback_subject=subject)
        if messages:
            logger.info(f"parse_eml: extracted {len(messages)} messages from thread")
            return messages
        logger.warning("parse_eml: thread split failed, treating as single message")

    return [{
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
    }]