from django.db import models
import uuid


class TimestampModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UUIDModel(models.Model):
    id = models.CharField(primary_key=True, max_length=36, default=uuid.uuid4, unique=True, null=False)

    class Meta:
        abstract = True