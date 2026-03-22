import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def send_otp_email(to_email: str, code: str) -> bool:
    """
    Send OTP code via Mailgun. Returns True if successful.
    """
    try:
        response = requests.post(
            f"https://api.mailgun.net/v3/{settings.MAILGUN_DOMAIN}/messages",
            auth=("api", settings.MAILGUN_API_KEY),
            data={
                "from": f"Ezer <{settings.MAILGUN_FROM}>",
                "to": to_email,
                "subject": f"{code} is your Ezer login code",
                "text": f"Your Ezer login code is: {code}\n\nThis code expires in 10 minutes.\n\nIf you didn't request this, you can ignore this email.",
                "html": f"""
                <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:480px;margin:0 auto;padding:32px 24px;">
                  <h2 style="font-size:20px;font-weight:600;margin-bottom:8px;">Your Ezer login code</h2>
                  <p style="color:#666;margin-bottom:24px;">Enter this code to sign in to Ezer.</p>
                  <div style="background:#f5f5f0;border-radius:8px;padding:24px;text-align:center;margin-bottom:24px;">
                    <span style="font-size:36px;font-weight:700;letter-spacing:8px;">{code}</span>
                  </div>
                  <p style="color:#999;font-size:13px;">This code expires in 10 minutes. If you didn't request this, you can ignore this email.</p>
                </div>
                """,
            },
        )
        response.raise_for_status()
        logger.info(f"OTP email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send OTP email to {to_email}: {e}")
        return False