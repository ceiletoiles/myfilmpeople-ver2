from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from .password_reset_forms import PasswordResetConfirmForm, PasswordResetRequestForm
from .password_reset_services import (
	create_password_reset_token,
	PASSWORD_RESET_EXPIRY_MINUTES,
	fingerprint_ip,
	fingerprint_value,
	find_password_reset_token,
	get_client_ip,
	is_password_reset_request_rate_limited,
	is_password_reset_verify_rate_limited,
	is_reset_token_usable,
	log_password_reset_event,
	mark_reset_token_used,
	register_reset_token_attempt,
	send_password_reset_email,
)


logger = logging.getLogger(__name__)


def _generic_request_message() -> str:
	return "If an account with that email exists, a password reset link has been sent."


def password_reset_request(request: HttpRequest) -> HttpResponse:
	"""Accept an email address and send a password reset link if the user exists."""

	form = PasswordResetRequestForm(request.POST or None)

	if request.method == "POST" and form.is_valid():
		email = form.cleaned_data["email"]
		ip_address = get_client_ip(request)
		email_hash = fingerprint_value(email)
		ip_hash = fingerprint_ip(ip_address)

		if is_password_reset_request_rate_limited(email_hash=email_hash, ip_hash=ip_hash):
			log_password_reset_event(
				action="request",
				email_hash=email_hash,
				ip_hash=ip_hash,
				rate_limited=True,
			)
			messages.success(request, _generic_request_message())
			return redirect("password_reset_request")

		User = get_user_model()
		user = User.objects.filter(email__iexact=email).first()
		log_password_reset_event(
			action="request",
			email_hash=email_hash,
			ip_hash=ip_hash,
			user=user,
			success=bool(user),
		)
		if user is not None:
			raw_token, token = create_password_reset_token(user)
			reset_url = f"{request.build_absolute_uri(reverse('password_reset_confirm'))}?token={raw_token}"
			try:
				send_password_reset_email(user=user, reset_url=reset_url)
			except Exception:
				logger.exception("Failed to send password reset email for %s", email)
				token.delete()

		messages.success(request, _generic_request_message())
		return redirect("password_reset_request")

	return render(
		request,
		"accounts/password_reset_request.html",
		{
			"form": form,
		},
	)


def password_reset_confirm(request: HttpRequest) -> HttpResponse:
	"""Validate the reset token and allow the user to set a new password."""

	raw_token = (request.GET.get("token") or request.POST.get("token") or "").strip()
	token = find_password_reset_token(raw_token) if raw_token else None
	ip_address = get_client_ip(request)
	ip_hash = fingerprint_ip(ip_address)

	if is_password_reset_verify_rate_limited(ip_hash=ip_hash):
		log_password_reset_event(action="verify", ip_hash=ip_hash, rate_limited=True)
		messages.error(request, "This reset link is temporarily unavailable. Please request a new one.")
		return redirect("password_reset_request")

	if token is None:
		if raw_token:
			log_password_reset_event(action="verify", ip_hash=ip_hash, success=False)
		messages.error(request, "That password reset link is invalid or expired.")
		return redirect("password_reset_request")

	is_usable, _reason = is_reset_token_usable(token)
	if not is_usable:
		register_reset_token_attempt(token)
		log_password_reset_event(action="verify", ip_hash=ip_hash, user=token.user, success=False)
		messages.error(request, "That password reset link is invalid or expired.")
		return redirect("password_reset_request")

	user = token.user
	form = PasswordResetConfirmForm(user, request.POST or None)
	log_password_reset_event(action="verify", ip_hash=ip_hash, user=user, success=True)

	if request.method == "POST":
		if form.is_valid():
			form.save()
			mark_reset_token_used(token)
			log_password_reset_event(action="verify", ip_hash=ip_hash, user=user, success=True)
			messages.success(request, "Your password has been reset. You can now sign in.")
			return redirect("login")

		register_reset_token_attempt(token)
		log_password_reset_event(action="verify", ip_hash=ip_hash, user=user, success=False)

	return render(
		request,
		"accounts/password_reset_confirm.html",
		{
			"form": form,
			"token": raw_token,
			"expires_minutes": PASSWORD_RESET_EXPIRY_MINUTES,
			"invalid_token": False,
		},
	)
