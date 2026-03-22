import logging

from django.contrib.auth import login, logout
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.authenticate.email import send_otp_email
from apps.authenticate.models import OTPCode
from apps.user.models import User
from django.contrib import messages
import sys

logger = logging.getLogger(__name__)


def login_view(request):
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()

        if not email:
            return render(request, "authenticate/login.html", {
                "error": "Please enter your email address."
            })

        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "username": email,
                "is_active": True,
                "name": email.split("@")[0],
            }
        )

        OTPCode.objects.filter(user=user, used_at__isnull=True).delete()
        _, code = OTPCode.issue(user=user, ttl_minutes=10)

        sent = send_otp_email(email, code)

        if not sent:
            # Fall back to logs if email fails so you're never locked out
            print(f"\n*** OTP for {email}: {code} ***\n", flush=True, file=sys.stdout)
            logger.warning(f"Email failed, OTP logged for {email}: {code}")

        request.session["otp_email"] = email

        if created:
            request.session["is_new_user"] = True

        return redirect("/auth/verify/")

    return render(request, "authenticate/login.html")


def verify_view(request):
    email = request.POST.get("email") or request.session.get("otp_email", "")

    if not email:
        return redirect("/auth/login/")

    if request.method == "POST":
        otp = request.POST.get("otp", "").strip()

        try:
            user = User.objects.get(email=email, is_active=True)
        except User.DoesNotExist:
            return render(request, "authenticate/verify.html", {
                "email": email,
                "error": "User not found."
            })

        otp_obj = (
            OTPCode.objects.filter(
                user=user,
                used_at__isnull=True,
                expires_at__gt=timezone.now(),
            )
            .order_by("-created_at")
            .first()
        )

        if not otp_obj or not otp_obj.can_verify():
            return render(request, "authenticate/verify.html", {
                "email": email,
                "error": "Code expired or invalid. Please request a new one."
            })

        if not otp_obj.verify(otp):
            otp_obj.failed_attempts += 1
            otp_obj.save(update_fields=["failed_attempts"])
            return render(request, "authenticate/verify.html", {
                "email": email,
                "error": "Incorrect code. Please try again."
            })

        otp_obj.mark_used()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        request.session.pop("otp_email", None)

        if request.session.pop("is_new_user", False):
            messages.success(request, "Welcome to Ezer! Create your first project to get started.")

        return redirect("/")

    return render(request, "authenticate/verify.html", {"email": email})


def logout_view(request):
    logout(request)
    return redirect("/auth/login/")