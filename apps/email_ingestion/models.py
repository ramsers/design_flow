from django.db import models
from django.conf import settings

from apps.shared.models import UUIDModel, TimestampModel
from .value_objects import ArtifactKind, CheckpointStatus, IngestedVia, SnapshotConfidence



class Project(UUIDModel, TimestampModel):
    name = models.CharField(max_length=255)
    project_number = models.CharField(max_length=50, blank=True)
    client_name = models.CharField(max_length=255)
    client_email = models.EmailField(blank=True)
    capture_address = models.EmailField(unique=True)
    address = models.CharField(max_length=500, blank=True)
    sqft = models.PositiveIntegerField(null=True, blank=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="ProjectMembership",
        related_name="projects",
    )

    def __str__(self):
        return self.name

    class Meta:
        db_table = "projects"


class ProjectStakeholder(UUIDModel, TimestampModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="stakeholders")
    name = models.CharField(max_length=255)
    role = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    company = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "project_stakeholders"

    def __str__(self):
        return f"{self.name} ({self.role}) — {self.project.name}"


class ProjectMembership(UUIDModel, TimestampModel):
    ROLE_CHOICES = [
        ("owner", "Owner"),
        ("member", "Member"),
    ]
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="member")

    class Meta:
        db_table = "project_memberships"
        unique_together = ("project", "user")

    def __str__(self):
        return f"{self.user} — {self.project.name} ({self.role})"


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
    thread_id = models.UUIDField(null=True, blank=True, db_index=True)
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
    confidence = models.CharField(max_length=10, choices=SnapshotConfidence.choices)
    status = models.CharField(max_length=20, choices=CheckpointStatus.choices)

    def __str__(self):
        return f"Snapshot for {self.checkpoint}"

    class Meta:
        db_table = "snapshots"