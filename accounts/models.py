
from __future__ import annotations

from django.conf import settings
from django.db import models


class BadgeNotification(models.Model):
	"""Server-persisted badge notification for a user.

	Created when a user's follow count crosses a badge threshold. `seen`
	is toggled when the client acknowledges the celebration so we don't
	re-notify on subsequent page loads.
	"""
	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="badge_notifications")
	level = models.IntegerField()
	min_count = models.IntegerField()
	label = models.CharField(max_length=200)
	image = models.CharField(max_length=255, blank=True)
	seen = models.BooleanField(default=False)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ["-level", "-created_at"]

	def __str__(self) -> str:  # pragma: no cover - convenience
		return f"BadgeNotification(user={self.user_id}, level={self.level}, seen={self.seen})"


class EmailVerification(models.Model):
	"""Per-user email verification state for existing accounts.

	We keep a lightweight record indicating whether a user's email has
	been verified independently of `is_active` (which is used for
	pending signups). Created lazily.
	"""
	user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="email_verification")
	email_verified = models.BooleanField(default=False)
	verified_via_signup = models.BooleanField(
		default=False,
		help_text="True when the account was created through the email-OTP signup flow.",
	)

	def __str__(self) -> str:  # pragma: no cover - convenience
		return f"EmailVerification(user={self.user_id}, verified={self.email_verified})"


class PasswordResetToken(models.Model):
	"""Hashed password reset token for a user.

	The raw token is never stored. Tokens expire quickly and are marked used
	after a successful password reset.
	"""

	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="password_reset_tokens")
	token_hash = models.CharField(max_length=64, unique=True)
	created_at = models.DateTimeField(auto_now_add=True)
	expires_at = models.DateTimeField(db_index=True)
	used_at = models.DateTimeField(null=True, blank=True, db_index=True)
	verification_attempts = models.PositiveIntegerField(default=0)
	last_attempt_at = models.DateTimeField(null=True, blank=True)

	class Meta:
		ordering = ["-created_at"]
		indexes = [
			models.Index(fields=["user", "expires_at"]),
			models.Index(fields=["expires_at", "used_at"]),
		]

	def __str__(self) -> str:  # pragma: no cover - convenience
		state = "used" if self.used_at else "active"
		return f"PasswordResetToken(user={self.user_id}, {state})"

	@property
	def is_expired(self) -> bool:
		from django.utils import timezone

		return timezone.now() >= self.expires_at

	@property
	def is_used(self) -> bool:
		return self.used_at is not None


class PasswordResetRequestLog(models.Model):
	"""Lightweight audit trail for password reset requests and verification attempts."""

	ACTION_REQUEST = "request"
	ACTION_VERIFY = "verify"
	ACTION_CHOICES = [
		(ACTION_REQUEST, "Request"),
		(ACTION_VERIFY, "Verify"),
	]

	action = models.CharField(max_length=20, choices=ACTION_CHOICES)
	email_hash = models.CharField(max_length=64, blank=True, default="")
	ip_hash = models.CharField(max_length=64, blank=True, default="")
	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="password_reset_logs")
	success = models.BooleanField(default=False)
	rate_limited = models.BooleanField(default=False)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ["-created_at"]
		indexes = [
			models.Index(fields=["action", "created_at"]),
			models.Index(fields=["email_hash", "created_at"]),
			models.Index(fields=["ip_hash", "created_at"]),
		]

	def __str__(self) -> str:  # pragma: no cover - convenience
		return f"PasswordResetRequestLog(action={self.action}, success={self.success})"
