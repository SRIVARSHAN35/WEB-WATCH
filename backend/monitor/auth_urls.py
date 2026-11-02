from django.urls import path

from . import auth_views


urlpatterns = [
    path("register/", auth_views.register, name="auth-register"),
    path("login/", auth_views.login, name="auth-login"),
    path("google/", auth_views.google_login, name="auth-google"),
    path("google/link/", auth_views.link_google, name="auth-google-link"),
    path("logout/", auth_views.logout, name="auth-logout"),
    path("me/", auth_views.me, name="auth-me"),
    path("password-reset/", auth_views.password_reset_request, name="auth-password-reset-request"),
    path("password-reset/validate/", auth_views.password_reset_validate, name="auth-password-reset-validate"),
    path("password-reset/confirm/", auth_views.password_reset_confirm, name="auth-password-reset-confirm"),
    path("password/change/", auth_views.change_password, name="auth-password-change"),
    path("delete-account/", auth_views.delete_account, name="auth-delete-account"),
]
