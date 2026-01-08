from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.authenticate.models import OTPCode
from apps.authenticate.validators import RequestOtpValidator, OTPValidator
from apps.user.models import User
from apps.user.serializers import UserSerializer
from rest_framework_simplejwt.views import TokenRefreshView as BaseTokenRefreshView


class OTPRequestView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        validator = RequestOtpValidator(data=request.data)
        validator.is_valid(raise_exception=True)

        user = User.objects.get(email=validator.data['email'])
        OTPCode.objects.filter(user=user).delete()
        OTPCode.issue(user=user, ttl_minutes=10)

        return Response(status=status.HTTP_204_NO_CONTENT)


class OTPVerifyView(APIView):
    permission_classes = [AllowAny]
    def post(self, request):
        validator = OTPValidator(data=request.data)
        validator.is_valid(raise_exception=True)

        user = User.objects.get(email=validator.data['email'])
        refresh = RefreshToken.for_user(user)
        data = {"access": str(refresh.access_token), "refresh": str(refresh), "user": UserSerializer(user).data}

        return Response(data=data, status=status.HTTP_200_OK)


class TokenRefreshView(BaseTokenRefreshView):
    permission_classes = [AllowAny]


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh = request.data.get("refresh")
        if not refresh:
            return Response({"detail": "refresh token required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            RefreshToken(refresh).blacklist()
        except Exception:
            return Response({"detail": "invalid refresh token"}, status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_204_NO_CONTENT)
