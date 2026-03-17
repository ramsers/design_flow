from django.urls import path
from . import views

urlpatterns = [
    path("inbound/email/", views.inbound_email, name="inbound_email"),
]