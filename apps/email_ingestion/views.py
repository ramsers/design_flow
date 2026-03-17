import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Artifact, Project
from .validators import InboundEmailValidator

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

    artifact = Artifact.objects.create(
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

    logger.info(f"Artifact {artifact.id} created for project {project.name}")
    return JsonResponse({"status": "ok", "artifact_id": str(artifact.id)})