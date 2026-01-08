from django.utils import timezone
from rest_framework import serializers

from apps.authenticate.models import OTPCode
from apps.user.models import User


class RequestOtpValidator(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        try:
            User.objects.get(email=value)
        except User.DoesNotExist:
            raise serializers.ValidationError('user_not_found')

        return value


class OTPValidator(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(min_length=4, max_length=10)

    def validate(self, attrs):
        user = User.objects.filter(email=attrs.get('email'), is_active=True).first()
        otp = attrs.get('otp')

        if not user:
            raise serializers.ValidationError('user_not_found')

        otp_obj = (
            OTPCode.objects.select_for_update()
            .filter(user=user, used_at__isnull=True, expires_at__gt=timezone.now())
            .order_by("-created_at")
            .first()
        )

        if not otp_obj and not otp_obj.can_verify():
            raise serializers.ValidationError('otp_not_found')

        otp_obj.failed_attempts += 1
        otp_obj.save(update_fields=["failed_attempts"])

        if not otp_obj.verify(otp):
            raise ValueError("invalid")

        otp_obj.mark_used()
        return attrs
