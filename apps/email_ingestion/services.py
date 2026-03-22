import json
import logging
import os

import anthropic
from django.conf import settings

from .models import Artifact, Checkpoint, Project
from .value_objects import CheckpointStatus, SnapshotConfidence
import re
import uuid
logger = logging.getLogger(__name__)



def generate_capture_address(project_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]", "-", project_name.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)[:40]
    domain = os.environ.get("MAILGUN_DOMAIN")
    base = f"{slug}@{domain}"
    if Project.objects.filter(capture_address=base).exists():
        base = f"{slug}-{str(uuid.uuid4())[:4]}@{domain}"
    return base


def infer_checkpoints(project_id: str) -> list:
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        logger.warning(f"infer_checkpoints: project {project_id} not found")
        return []

    unlinked = Artifact.objects.filter(
        project=project,
        checkpoint__isnull=True,
    ).order_by("thread_id", "received_at")

    if not unlinked.exists():
        logger.info(f"infer_checkpoints: no unlinked artifacts for {project.name}")
        return []

    existing = Checkpoint.objects.filter(project=project).values("id", "scope_label")
    existing_context = "\n".join(
        f"- id={c['id']} label={c['scope_label']}" for c in existing
    ) or "None"

    # Group artifacts by thread_id
    from collections import defaultdict
    threads = defaultdict(list)
    for a in unlinked:
        key = str(a.thread_id) if a.thread_id else str(a.id)
        threads[key].append(a)

    ai_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    checkpoints_touched = []

    for thread_key, artifacts in threads.items():
        artifacts_sorted = sorted(
            artifacts,
            key=lambda a: a.received_at or a.created_at
        )

        # Build thread context with artifact IDs clearly labelled
        thread_context = ""
        for a in artifacts_sorted:
            thread_context += (
                f"[artifact_id: {a.id}]\n"
                f"From: {a.sender}\n"
                f"Date: {a.received_at or 'unknown'}\n"
                f"Subject: {a.subject}\n\n"
                f"{a.text_content[:1500]}\n\n---\n\n"
            )

        prompt = f"""You are analyzing an email thread for a project called "{project.name}" (client: {project.client_name}).

Read the full conversation and identify the current open decision points — where things stand RIGHT NOW at the end of the thread.

Each artifact in the thread has an artifact_id label. You must use these exact IDs in your response.

Existing checkpoints in this project:
{existing_context}

Full email thread (oldest to newest):
{thread_context}

Return one checkpoint per distinct decision topic. For each checkpoint, specify exactly which artifact_ids are relevant to it.

Respond with a JSON array only, no preamble, no markdown:
[
  {{
    "scope_label": "Area — Specific Decision",
    "status": "approved | ambiguous | blocked | pending",
    "artifact_ids": ["<artifact_id>", "<artifact_id>"],
    "checkpoint_id": "<existing checkpoint id or null>"
  }}
]

Rules:
- One checkpoint per distinct topic — not one per email
- artifact_ids must be exact IDs from the [artifact_id: ...] labels above
- Use the FINAL state of each topic as the status
- approved: clear explicit yes at the END of the thread
- blocked: unresolved concern, rejection, or blocker at the END of the thread
- pending: waiting on someone to respond, decide, or act
- ambiguous: unclear where it stands
- Skip topics fully resolved with no further action needed
- If no trackable decisions exist, return []
"""

        try:
            message = ai_client.messages.create(
                model="claude-opus-4-5",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text
            clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
            results = json.loads(clean)
        except json.JSONDecodeError as e:
            logger.error(f"infer_checkpoints: JSON parse failed for thread {thread_key}: {e}")
            continue
        except Exception as e:
            logger.error(f"infer_checkpoints: AI call failed for thread {thread_key}: {e}")
            continue

        # Build a lookup map for quick artifact access
        artifact_map = {str(a.id): a for a in artifacts}

        for item in results:
            scope_label = item.get("scope_label", "Unlabelled Decision")
            status = item.get("status", CheckpointStatus.PENDING)
            if status not in CheckpointStatus.values:
                status = CheckpointStatus.AMBIGUOUS

            # Check if linking to existing checkpoint
            checkpoint_id = item.get("checkpoint_id")
            checkpoint = None

            if checkpoint_id:
                try:
                    checkpoint = Checkpoint.objects.get(
                        id=checkpoint_id, project=project
                    )
                    logger.info(f"infer_checkpoints: linking to existing '{checkpoint.scope_label}'")
                except Checkpoint.DoesNotExist:
                    pass

            if checkpoint is None:
                checkpoint = Checkpoint.objects.create(
                    project=project,
                    scope_label=scope_label,
                    status=status,
                )
                logger.info(f"infer_checkpoints: created '{scope_label}' → {status}")

            # Link only the specific artifacts the AI assigned to this checkpoint
            linked_count = 0
            artifact_ids = item.get("artifact_ids", [])

            for artifact_id in artifact_ids:
                artifact = artifact_map.get(str(artifact_id))
                if artifact:
                    artifact.checkpoint = checkpoint
                    artifact.save(update_fields=["checkpoint", "updated_at"])
                    linked_count += 1
                else:
                    logger.warning(f"infer_checkpoints: artifact_id {artifact_id} not found in thread")

            # If AI returned no artifact_ids or none matched,
            # fall back to linking all unlinked artifacts in this thread
            if linked_count == 0:
                logger.warning(f"infer_checkpoints: no artifacts linked for '{scope_label}', linking all thread artifacts")
                for a in artifacts:
                    if not a.checkpoint_id:
                        a.checkpoint = checkpoint
                        a.save(update_fields=["checkpoint", "updated_at"])

            checkpoints_touched.append(checkpoint)
            logger.info(f"infer_checkpoints: '{scope_label}' → {linked_count} artifacts linked")

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

    for a in artifacts:
        print(f"\n=== ARTIFACT TEXT for checkpoint {checkpoint.scope_label} ===\n{a.text_content[:500]}\n===\n",
              flush=True)

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
      "waiting_on": "Name or role of who needs to act next. For approved items this is the Designer (to execute). For blocked/pending items this is the Client (to decide). Use actual name if identifiable, otherwise use role.",
      "status": "approved | ambiguous | blocked | pending",
      "confidence": "high | medium | low"
    }}

    Rules:
    - approved: client has clearly and explicitly said yes to this item
    - ambiguous: some positive signals but no clear explicit approval
    - blocked: client raised a concern, requested a change, or said no
    - pending: no client response yet
    - waiting_on: who needs to act next to move this forward
    """

    ai_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    try:
        message = ai_client.messages.create(
            model="claude-opus-4-5",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text
        print(f"\n=== SNAPSHOT RAW for {checkpoint_id} ===\n{raw}\n===\n", flush=True)
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
        result = json.loads(clean)
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

