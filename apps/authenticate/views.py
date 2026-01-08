from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.authenticate.models import OTPCode
from apps.authenticate.validators import RequestOtpValidator, OTPValidator
from apps.user.models import User
from apps.user.serializers import UserSerializer


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
