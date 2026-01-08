import hashlib
import secrets
from datetime import timezone, timedelta

from django.db import models

from config import settings


class OTPCode(models.Model):
    PURPOSE_LOGIN = "login"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="otp_codes")
    purpose = models.CharField(max_length=32, default=PURPOSE_LOGIN)

    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(blank=True, null=True)

    failed_attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)


    class Meta:
        indexes = [
            models.Index(fields=["user", "purpose", "expires_at"]),
            models.Index(fields=["user", "purpose", "used_at"]),
        ]

    @staticmethod
    def _hash(code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    @classmethod
    def issue(cls, user, ttl_minutes: int = 10, purpose: str = PURPOSE_LOGIN):
        code = f"{secrets.randbelow(1_000_000):06d}"  # 6-digit
        obj = cls.objects.create(
            user=user,
            purpose=purpose,
            code_hash=cls._hash(code),
            expires_at=timezone.now() + timedelta(minutes=ttl_minutes),
        )
        return obj, code

    def can_verify(self) -> bool:
        if self.used_at is not None:
            return False
        if timezone.now() > self.expires_at:
            return False
        if self.failed_attempts >= self.max_attempts:
            return False
        return True

    def verify(self, code: str) -> bool:
        return self.code_hash == self._hash(code)

    def mark_used(self):
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])

    class Meta:
        ordering = ['-created_at']
        db_table = "otp_codes"