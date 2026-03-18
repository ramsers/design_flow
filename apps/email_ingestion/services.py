import json
import logging

import anthropic
from django.conf import settings

from .models import Artifact, Checkpoint, Project
from .value_objects import CheckpointStatus, SnapshotConfidence

logger = logging.getLogger(__name__)


def infer_checkpoints(project_id: str) -> list:
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        logger.warning(f"infer_checkpoints: project {project_id} not found")
        return []

    unlinked = Artifact.objects.filter(
        project=project,
        checkpoint__isnull=True,
    ).order_by("received_at")

    if not unlinked.exists():
        logger.info(f"infer_checkpoints: no unlinked artifacts for {project.name}")
        return []

    existing = Checkpoint.objects.filter(project=project).values("id", "scope_label")
    existing_context = "\n".join(
        f"- id={c['id']} label={c['scope_label']}" for c in existing
    ) or "None"

    artifact_blocks = []
    for a in unlinked:
        artifact_blocks.append(
            f"artifact_id: {a.id}\n"
            f"From: {a.sender}\n"
            f"Subject: {a.subject}\n\n"
            f"{a.text_content[:1000]}"
        )

    artifacts_context = "\n\n---\n\n".join(artifact_blocks)

    prompt = f"""You are analyzing emails for a project called "{project.name}" (client: {project.client_name}).

Identify decision points — approvals, change requests, procurement sign-offs, blocking issues.

Existing checkpoints:
{existing_context}

Emails:
{artifacts_context}

Respond with a JSON array only, no preamble, no markdown:
[
  {{
    "artifact_id": "<id>",
    "is_decision_point": true,
    "checkpoint_id": "<existing id or null>",
    "scope_label": "<short label if new, e.g. 'Kitchen — Island Dimensions'>",
    "status": "approved | ambiguous | blocked | pending"
  }}
]

Rules:
- Skip scheduling, pleasantries, logistics — set is_decision_point to false
- If uncertain, set is_decision_point to true and status to ambiguous
- scope_label format: "Area — Topic"
"""

    ai_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    try:
        message = ai_client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text
        results = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"infer_checkpoints: JSON parse failed: {e}")
        return []
    except Exception as e:
        logger.error(f"infer_checkpoints: AI call failed: {e}")
        return []

    checkpoints_touched = []

    for item in results:
        if not item.get("is_decision_point"):
            continue

        artifact_id = item.get("artifact_id")
        try:
            artifact = Artifact.objects.get(id=artifact_id, project=project)
        except Artifact.DoesNotExist:
            logger.warning(f"infer_checkpoints: artifact {artifact_id} not found, skipping")
            continue

        status = item.get("status", CheckpointStatus.PENDING)
        if status not in CheckpointStatus.values:
            status = CheckpointStatus.AMBIGUOUS

        checkpoint_id = item.get("checkpoint_id")
        checkpoint = None

        if checkpoint_id:
            try:
                checkpoint = Checkpoint.objects.get(id=checkpoint_id, project=project)
                logger.info(f"infer_checkpoints: linking to existing checkpoint '{checkpoint.scope_label}'")
            except Checkpoint.DoesNotExist:
                logger.warning(f"infer_checkpoints: checkpoint {checkpoint_id} not found, will create new")

        if checkpoint is None:
            scope_label = item.get("scope_label", "Unlabelled Decision")
            checkpoint = Checkpoint.objects.create(
                project=project,
                scope_label=scope_label,
                status=status,
            )
            logger.info(f"infer_checkpoints: created '{scope_label}' → {status}")

        artifact.checkpoint = checkpoint
        artifact.save(update_fields=["checkpoint", "updated_at"])
        logger.info(f"infer_checkpoints: linked '{artifact.subject}' → '{checkpoint.scope_label}'")

        checkpoints_touched.append(checkpoint)

    logger.info(f"infer_checkpoints: done — {len(checkpoints_touched)} checkpoints touched")
    return checkpoints_touched


def generate_snapshot(checkpoint_id: str) -> None:
    from .models import Snapshot

    try:
        checkpoint = Checkpoint.objects.prefetch_related("artifacts").get(id=checkpoint_id)
    except Checkpoint.DoesNotExist:
        logger.warning(f"generate_snapshot: checkpoint {checkpoint_id} not found")
        return None

    artifacts = checkpoint.artifacts.order_by("received_at")
    if not artifacts.exists():
        logger.info(f"generate_snapshot: no artifacts on checkpoint {checkpoint_id}, skipping")
        return None

    email_blocks = []
    for a in artifacts:
        email_blocks.append(
            f"From: {a.sender}\n"
            f"Date: {a.received_at or 'unknown'}\n"
            f"Subject: {a.subject}\n\n"
            f"{a.text_content[:1500]}"
        )

    context = "\n\n---\n\n".join(email_blocks)

    prompt = f"""You are reviewing email correspondence for a project checkpoint.

    Checkpoint: {checkpoint.scope_label}
    Project: {checkpoint.project.name}

    Emails:
    {context}

    Respond with JSON only, no preamble, no markdown:
    {{
      "summary": "One sentence plain-English summary of where this decision stands.",
      "waiting_on": "Name or role of who needs to act next to move this forward. For approved items, this is the 
      Designer (to execute). For blocked/pending items, this is the Client (to decide). Use the actual name if 
      identifiable from the emails, otherwise use their role.",
      "status": "approved | ambiguous | blocked | pending",
      "confidence": "high | medium | low"
    }}

    Rules:
    - approved: client has clearly and explicitly said yes to this item
    - ambiguous: some positive signals but no clear explicit approval
    - blocked: client raised a concern, requested a change, or said no
    - pending: no client response yet
    - waiting_on: who needs to act next to move this forward"""

    ai_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    try:
        message = ai_client.messages.create(
            model="claude-opus-4-5",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        result = json.loads(message.content[0].text)
    except json.JSONDecodeError as e:
        logger.error(f"generate_snapshot: JSON parse failed for {checkpoint_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"generate_snapshot: AI call failed for {checkpoint_id}: {e}")
        return None

    status = result.get("status", CheckpointStatus.PENDING)
    confidence = result.get("confidence", SnapshotConfidence.LOW)

    if status not in CheckpointStatus.values:
        status = CheckpointStatus.PENDING
    if confidence not in SnapshotConfidence.values:
        confidence = SnapshotConfidence.LOW

    snapshot, created = Snapshot.objects.update_or_create(
        checkpoint=checkpoint,
        defaults={
            "summary_text": result.get("summary", ""),
            "waiting_on": result.get("waiting_on", ""),
            "status": status,
            "confidence": confidence,
        },
    )

    checkpoint.status = status
    checkpoint.save(update_fields=["status", "updated_at"])

    logger.info(f"generate_snapshot: '{checkpoint.scope_label}' → {status} ({confidence})")
    print(f"\n=== SNAPSHOT: {checkpoint.scope_label} ===")
    print(f"Summary:    {snapshot.summary_text}")
    print(f"Waiting on: {snapshot.waiting_on}")
    print(f"Status:     {snapshot.status}")
    print(f"Confidence: {snapshot.confidence}")
    print("=" * 40)

    return snapshot


def process_project_artifacts(project_id: str) -> None:
    """
    Full pipeline: infer checkpoints from unlinked artifacts,
    then generate snapshots for all checkpoints that were touched.
    """
    checkpoints_touched = infer_checkpoints(project_id)

    for checkpoint in checkpoints_touched:
        generate_snapshot(str(checkpoint.id))

    logger.info(f"process_project_artifacts: done for project {project_id}")

