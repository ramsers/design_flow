import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Artifact, Project
from .services import process_project_artifacts
from .validators import InboundEmailValidator, EmlUploadValidator
from .parsers import parse_eml

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def inbound_email(request):
    validator = InboundEmailValidator(data=request.POST)

    if not validator.is_valid():
        logger.warning(f"Mailgun payload invalid: {validator.errors}")
        return JsonResponse({"errors": validator.errors}, status=400)

    data = validator.validated_data

    try:
        project = Project.objects.get(capture_address__iexact=data["recipient"])
    except Project.DoesNotExist:
        logger.warning(f"Inbound email to unknown address: {data['recipient']}")
        return JsonResponse({"status": "unknown address"}, status=200)

    Artifact.objects.create(
        project=project,
        kind="email",
        ingested_via="forward",
        subject=data.get("subject", ""),
        sender=data["sender"],
        recipient=data["recipient"],
        text_content=data.get("body_plain") or data.get("body_html") or "",
        metadata={
            "from": data["sender"],
            "to": data["recipient"],
            "message_id": data.get("message_id", ""),
            "date": data.get("date", ""),
        },
    )

    process_project_artifacts(str(project.id))

    return JsonResponse({"status": "ok"})


@csrf_exempt
@require_POST
def upload_eml(request, project_id):
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        return JsonResponse({"error": "project not found"}, status=404)

    validator = EmlUploadValidator(data={"files": request.FILES.getlist("files")})

    if not validator.is_valid():
        return JsonResponse({"errors": validator.errors}, status=400)

    files = validator.validated_data["files"]
    created_ids = []
    skipped = []

    for f in files:
        raw = f.read()
        parsed = parse_eml(raw)

        if not parsed:
            skipped.append(f.name)
            continue

        artifact = Artifact.objects.create(
            project=project,
            kind="email",
            ingested_via="upload",
            subject=parsed.get("subject", ""),
            sender=parsed.get("sender", ""),
            recipient=parsed.get("recipient", ""),
            text_content=parsed.get("text_content", ""),
            metadata=parsed.get("metadata", {}),
            received_at=parsed.get("received_at"),
        )
        created_ids.append(str(artifact.id))

    if created_ids:
        process_project_artifacts(str(project.id))

    logger.info(f"upload_eml: {len(created_ids)} artifacts created, {len(skipped)} skipped for project {project.name}")
    return JsonResponse({
        "status": "ok",
        "created": len(created_ids),
        "skipped": skipped,
        "artifact_ids": created_ids,
    })