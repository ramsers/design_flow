from django.contrib.auth.models import AbstractUser

from apps.shared.models import UUIDModel, TimestampModel
from django.db import models

class User(AbstractUser, UUIDModel, TimestampModel):
    email = models.EmailField(max_length=255, unique=True)
    name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    REQUIRED_FIELDS = []

    USERNAME_FIELD = 'email'

    class Meta:
        ordering = ['-created_at']
        db_table = "users"