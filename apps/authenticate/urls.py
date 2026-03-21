from django.urls import path
from . import dashboard_views

urlpatterns = [
    path("login/", dashboard_views.login_view, name="login"),
    path("verify/", dashboard_views.verify_view, name="verify"),
    path("logout/", dashboard_views.logout_view, name="logout"),
]