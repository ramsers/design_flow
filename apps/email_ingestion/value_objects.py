from django.db import models


class CheckpointStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    AMBIGUOUS = "ambiguous", "Ambiguous"
    BLOCKED = "blocked", "Blocked"


class ArtifactKind(models.TextChoices):
    EMAIL = "email", "Email"


class IngestedVia(models.TextChoices):
    FORWARD = "forward", "Forward"
    UPLOAD = "upload", "Upload"


class SnapshotConfidence(models.TextChoices):
    HIGH = "high", "High"
    MEDIUM = "medium", "Medium"
    LOW = "low", "Low"