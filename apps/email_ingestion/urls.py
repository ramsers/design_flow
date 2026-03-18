from django.urls import path
from . import views

urlpatterns = [
    path("inbound/email/", views.inbound_email, name="inbound_email"),
    path("projects/<str:project_id>/upload/", views.upload_eml, name="upload_eml"),
]