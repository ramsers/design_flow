import logging
import uuid

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Artifact, Project, ProjectMembership
from .services import process_project_artifacts, generate_capture_address
from .validators import InboundEmailValidator, EmlUploadValidator
from .parsers import parse_eml
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required

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
        thread_id=uuid.uuid4(),
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


@login_required
def project_list(request):
    projects = Project.objects.filter(
        memberships__user=request.user
    ).prefetch_related("checkpoints", "artifacts").order_by("-created_at")
    return render(request, "email_ingestion/project_list.html", {"projects": projects})


@login_required
def project_detail(request, project_id):
    project = get_object_or_404(Project, id=project_id, memberships__user=request.user)
    checkpoints = project.checkpoints.prefetch_related("artifacts", "snapshot").order_by("-created_at")
    unlinked_artifacts = project.artifacts.filter(checkpoint__isnull=True).order_by("-created_at")
    return render(request, "email_ingestion/project_detail.html", {
        "project": project,
        "checkpoints": checkpoints,
        "unlinked_artifacts": unlinked_artifacts,
    })

@login_required
def create_project(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        client_name = request.POST.get("client_name", "").strip()
        client_email = request.POST.get("client_email", "").strip()
        address = request.POST.get("address", "").strip()
        project_number = request.POST.get("project_number", "").strip()
        sqft = request.POST.get("sqft", "").strip()

        if not name or not client_name:
            return render(request, "email_ingestion/create_project.html", {
                "error": "Project name and client name are required.",
                "name": name,
                "client_name": client_name,
                "client_email": client_email,
                "address": address,
                "project_number": project_number,
                "sqft": sqft,
            })

        capture_address = generate_capture_address(name)

        project = Project.objects.create(
            name=name,
            client_name=client_name,
            client_email=client_email,
            capture_address=capture_address,
            address=address,
            project_number=project_number,
            sqft=int(sqft) if sqft.isdigit() else None,
        )

        ProjectMembership.objects.create(
            project=project,
            user=request.user,
            role="owner",
        )

        return redirect(f"/projects/{project.id}/")

    return render(request, "email_ingestion/create_project.html")


@login_required
def upload_eml_view(request, project_id):
    project = get_object_or_404(Project, id=project_id, memberships__user=request.user)

    if request.method != "POST":
        return redirect(f"/projects/{project_id}/")

    from .validators import EmlUploadValidator

    validator = EmlUploadValidator(data={"files": request.FILES.getlist("files")})

    if not validator.is_valid():
        messages.error(request, "Invalid files. Please upload .eml files only.")
        return redirect(f"/projects/{project_id}/")

    files = validator.validated_data["files"]
    created = 0
    skipped = []

    for f in files:
        raw = f.read()
        parsed_messages = parse_eml(raw)

        if not parsed_messages:
            skipped.append(f.name)
            continue

        # All messages from the same file share a thread_id
        thread_id = uuid.uuid4()

        for parsed in parsed_messages:
            Artifact.objects.create(
                project=project,
                thread_id=thread_id,
                kind="email",
                ingested_via="upload",
                subject=parsed.get("subject", ""),
                sender=parsed.get("sender", ""),
                recipient=parsed.get("recipient", ""),
                text_content=parsed.get("text_content", ""),
                metadata=parsed.get("metadata", {}),
                received_at=parsed.get("received_at"),
            )
            created += 1

    if created:
        process_project_artifacts(str(project.id))
        messages.success(request, f"{created} email{'s' if created > 1 else ''} uploaded and analyzed.")
    else:
        messages.error(request, f"No valid emails found. {len(skipped)} file(s) skipped.")

    return redirect(f"/projects/{project_id}/")


@login_required
def edit_project(request, project_id):
    project = get_object_or_404(Project, id=project_id, memberships__user=request.user)

    if request.method == "POST":
        action = request.POST.get("action")

        # ── Save project details ──────────────────────────────
        if action == "save_project":
            project.name = request.POST.get("name", "").strip() or project.name
            project.client_name = request.POST.get("client_name", "").strip() or project.client_name
            project.client_email = request.POST.get("client_email", "").strip()
            project.address = request.POST.get("address", "").strip()
            project.project_number = request.POST.get("project_number", "").strip()
            sqft = request.POST.get("sqft", "").strip()
            project.sqft = int(sqft) if sqft.isdigit() else None
            project.save()
            messages.success(request, "Project updated.")
            return redirect(f"/projects/{project_id}/edit/")

        # ── Add stakeholder ───────────────────────────────────
        elif action == "add_stakeholder":
            name = request.POST.get("sh_name", "").strip()
            if name:
                from .models import ProjectStakeholder
                ProjectStakeholder.objects.create(
                    project=project,
                    name=name,
                    role=request.POST.get("sh_role", "").strip(),
                    email=request.POST.get("sh_email", "").strip(),
                    phone=request.POST.get("sh_phone", "").strip(),
                    company=request.POST.get("sh_company", "").strip(),
                )
                messages.success(request, f"{name} added.")
            return redirect(f"/projects/{project_id}/edit/")

        # ── Remove stakeholder ────────────────────────────────
        elif action == "remove_stakeholder":
            from .models import ProjectStakeholder
            sh_id = request.POST.get("stakeholder_id")
            ProjectStakeholder.objects.filter(id=sh_id, project=project).delete()
            return redirect(f"/projects/{project_id}/edit/")

    stakeholders = project.stakeholders.all()
    return render(request, "email_ingestion/edit_project.html", {
        "project": project,
        "stakeholders": stakeholders,
    })