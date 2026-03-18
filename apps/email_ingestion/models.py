from django.db import models

from apps.shared.models import UUIDModel, TimestampModel
from .value_objects import ArtifactKind, CheckpointStatus, IngestedVia, SnapshotConfidence


class Project(UUIDModel, TimestampModel):
    name = models.CharField(max_length=255)
    client_name = models.CharField(max_length=255)
    client_email = models.EmailField(blank=True)
    capture_address = models.EmailField(unique=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "projects"


class Checkpoint(UUIDModel, TimestampModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="checkpoints")
    scope_label = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20,
        choices=CheckpointStatus.choices,
        default=CheckpointStatus.PENDING,
    )

    def __str__(self):
        return f"{self.project.name} — {self.scope_label}"

    class Meta:
        db_table = "checkpoints"


class Artifact(UUIDModel, TimestampModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="artifacts")
    checkpoint = models.ForeignKey(
        Checkpoint, on_delete=models.SET_NULL, null=True, blank=True, related_name="artifacts"
    )
    kind = models.CharField(
        max_length=20,
        choices=ArtifactKind.choices,
        default=ArtifactKind.EMAIL,
    )
    ingested_via = models.CharField(
        max_length=20,
        choices=IngestedVia.choices,
        default=IngestedVia.FORWARD,
    )
    subject = models.CharField(max_length=500, blank=True)
    sender = models.EmailField(blank=True)
    recipient = models.EmailField(blank=True)
    text_content = models.TextField(blank=True)
    metadata = models.JSONField(default=dict)
    received_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.kind} — {self.subject or '(no subject)'}"

    class Meta:
        db_table = "artifacts"


class Snapshot(UUIDModel, TimestampModel):
    checkpoint = models.OneToOneField(
        Checkpoint, on_delete=models.CASCADE, related_name="snapshot"
    )
    summary_text = models.TextField()
    waiting_on = models.CharField(max_length=255, blank=True, default="")
    confidence = models.CharField(
        max_length=10,
        choices=SnapshotConfidence.choices,
    )
    status = models.CharField(
        max_length=20,
        choices=CheckpointStatus.choices,
    )

    def __str__(self):
        return f"Snapshot for {self.checkpoint}"

    class Meta:
        db_table = "snapshots"