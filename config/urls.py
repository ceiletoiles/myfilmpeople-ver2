"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from accounts import views as accounts_views
from accounts import password_reset_views as password_reset_views
from catalog import views as catalog_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path("tmdb/<path:endpoint>/", catalog_views.tmdb_proxy, name="tmdb_proxy"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("signup/", accounts_views.signup, name="signup"),
    path("signup/verify/", accounts_views.signup_verify, name="signup_verify"),
    path("me/verify_email/", accounts_views.trigger_email_verification, name="trigger_email_verification"),
    path("me/", accounts_views.profile, name="user_profile"),
    path("forgot-password/", password_reset_views.password_reset_request, name="password_reset_request"),
    path("reset-password/", password_reset_views.password_reset_confirm, name="password_reset_confirm"),
    path("accounts/follow_status/", accounts_views.follow_status, name="follow_status"),
    path("accounts/badge_seen/", accounts_views.mark_badge_seen, name="mark_badge_seen"),
    path(
        "users/<str:username>/following/",
        accounts_views.user_following,
        name="user_following",
    ),
    path("", include("catalog.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
