from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .email_services import send_email_via_brevo
from .models import PasswordResetRequestLog, PasswordResetToken


PASSWORD_RESET_TOKEN_BYTES = 32
PASSWORD_RESET_EXPIRY_MINUTES = int(getattr(settings, "PASSWORD_RESET_TOKEN_EXPIRY_MINUTES", 15))
PASSWORD_RESET_REQUEST_LIMIT_PER_EMAIL = int(getattr(settings, "PASSWORD_RESET_REQUEST_LIMIT_PER_EMAIL", 3))
PASSWORD_RESET_REQUEST_LIMIT_PER_IP = int(getattr(settings, "PASSWORD_RESET_REQUEST_LIMIT_PER_IP", 10))
PASSWORD_RESET_VERIFY_LIMIT_PER_IP = int(getattr(settings, "PASSWORD_RESET_VERIFY_LIMIT_PER_IP", 20))
PASSWORD_RESET_VERIFY_LIMIT_PER_TOKEN = int(getattr(settings, "PASSWORD_RESET_VERIFY_LIMIT_PER_TOKEN", 5))
PASSWORD_RESET_LOOKBACK_MINUTES = int(getattr(settings, "PASSWORD_RESET_LOOKBACK_MINUTES", 15))


def _hmac_hex(value: str) -> str:
	key = (settings.SECRET_KEY or "").encode("utf-8")
	return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def fingerprint_value(value: str) -> str:
	return _hmac_hex((value or "").strip().lower())


def fingerprint_ip(ip_address: str) -> str:
	return _hmac_hex((ip_address or "").strip())


def hash_password_reset_token(raw_token: str) -> str:
	return _hmac_hex(raw_token)


def generate_password_reset_token() -> str:
	return secrets.token_urlsafe(PASSWORD_RESET_TOKEN_BYTES)


def get_client_ip(request) -> str:
	x_forwarded_for = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
	if x_forwarded_for:
		return x_forwarded_for
	return (request.META.get("REMOTE_ADDR") or "").strip()


def log_password_reset_event(
	*,
	action: str,
	email_hash: str = "",
	ip_hash: str = "",
	user=None,
	success: bool = False,
	rate_limited: bool = False,
) -> None:
	PasswordResetRequestLog.objects.create(
		action=action,
		email_hash=email_hash,
		ip_hash=ip_hash,
		user=user,
		success=success,
		rate_limited=rate_limited,
	)


def is_password_reset_request_rate_limited(*, email_hash: str, ip_hash: str) -> bool:
	since = timezone.now() - timedelta(minutes=PASSWORD_RESET_LOOKBACK_MINUTES)
	requests_for_email = PasswordResetRequestLog.objects.filter(
		action=PasswordResetRequestLog.ACTION_REQUEST,
		email_hash=email_hash,
		created_at__gte=since,
	).count()
	requests_for_ip = PasswordResetRequestLog.objects.filter(
		action=PasswordResetRequestLog.ACTION_REQUEST,
		ip_hash=ip_hash,
		created_at__gte=since,
	).count()
	return requests_for_email >= PASSWORD_RESET_REQUEST_LIMIT_PER_EMAIL or requests_for_ip >= PASSWORD_RESET_REQUEST_LIMIT_PER_IP


def is_password_reset_verify_rate_limited(*, ip_hash: str) -> bool:
	since = timezone.now() - timedelta(minutes=PASSWORD_RESET_LOOKBACK_MINUTES)
	verifications_for_ip = PasswordResetRequestLog.objects.filter(
		action=PasswordResetRequestLog.ACTION_VERIFY,
		ip_hash=ip_hash,
		created_at__gte=since,
	).count()
	return verifications_for_ip >= PASSWORD_RESET_VERIFY_LIMIT_PER_IP


def create_password_reset_token(user) -> tuple[str, PasswordResetToken]:
	raw_token = generate_password_reset_token()
	token_hash = hash_password_reset_token(raw_token)
	expires_at = timezone.now() + timedelta(minutes=PASSWORD_RESET_EXPIRY_MINUTES)
	with transaction.atomic():
		PasswordResetToken.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
		token = PasswordResetToken.objects.create(
			user=user,
			token_hash=token_hash,
			expires_at=expires_at,
		)
	return raw_token, token


def find_password_reset_token(raw_token: str) -> PasswordResetToken | None:
	token_hash = hash_password_reset_token(raw_token)
	return PasswordResetToken.objects.select_related("user").filter(token_hash=token_hash).first()


def is_reset_token_usable(token: PasswordResetToken) -> tuple[bool, str]:
	if token.used_at is not None:
		return False, "used"
	if timezone.now() >= token.expires_at:
		return False, "expired"
	if token.verification_attempts >= PASSWORD_RESET_VERIFY_LIMIT_PER_TOKEN:
		return False, "rate_limited"
	return True, ""


def register_reset_token_attempt(token: PasswordResetToken) -> None:
	token.verification_attempts = token.verification_attempts + 1
	token.last_attempt_at = timezone.now()
	token.save(update_fields=["verification_attempts", "last_attempt_at"])


def mark_reset_token_used(token: PasswordResetToken) -> None:
	now = timezone.now()
	PasswordResetToken.objects.filter(user=token.user, used_at__isnull=True).update(used_at=now)


def send_password_reset_email(*, user, reset_url: str) -> None:
	subject = "Reset your MyFilmPeople password"
	text_content = (
		f"Hi {user.username},\n\n"
		f"We received a request to reset your MyFilmPeople password.\n"
		f"Use this link to set a new password:\n{reset_url}\n\n"
		f"This link expires in {PASSWORD_RESET_EXPIRY_MINUTES} minutes.\n\n"
		"If you did not request this reset, you can ignore this email."
	)
	html_content = f"""
		<div style="font-family:Arial,sans-serif;line-height:1.6;color:#1f2937">
			<p>Hi {user.username},</p>
			<p>We received a request to reset your MyFilmPeople password.</p>
			<p>
				<a href="{reset_url}" style="display:inline-block;background:#111827;color:#ffffff;text-decoration:none;padding:12px 18px;border-radius:10px;font-weight:700">
					Reset Password
				</a>
			</p>
			<p>Or copy this link into your browser:</p>
			<p style="word-break:break-all">{reset_url}</p>
			<p>This link expires in {PASSWORD_RESET_EXPIRY_MINUTES} minutes.</p>
			<p>If you did not request this reset, you can ignore this email.</p>
		</div>
	""".strip()
	send_email_via_brevo(
		subject=subject,
		text_content=text_content,
		html_content=html_content,
		to_email=user.email,
		to_name=user.username,
		allow_smtp_fallback=False,
	)
