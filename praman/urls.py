"""Praman URL configuration.

Routes:
  /admin/         — Django admin (Day 8 customisations land here)
  /officer/       — officer-facing UI (Day 7 + Day 9-10)
  /accounts/      — Django auth: login, logout
  /              — redirect to /officer/
  /media/<path>   — uploaded documents during DEBUG (production should use
                    a proper object store + CDN)
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import RedirectView


urlpatterns = [
    path("", RedirectView.as_view(url="/officer/", permanent=False)),
    path("admin/", admin.site.urls),
    path("officer/", include("officer.urls")),
    # Login uses Django's built-in LoginView pointed at our template.
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(template_name="auth/login.html"),
        name="login",
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(next_page="/"), name="logout"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
