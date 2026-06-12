from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.mail import send_mail


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParsedSender:
	name: str
	email: str


def _parse_sender_address(raw_sender: str) -> ParsedSender:
	sender = (raw_sender or "").strip()
	if "<" in sender and ">" in sender:
		name_part, email_part = sender.split("<", 1)
		return ParsedSender(
			name=name_part.strip() or email_part.rstrip(">").strip(),
			email=email_part.rstrip(">").strip(),
		)
	return ParsedSender(name=sender, email=sender)


def send_email_via_brevo(
	*,
	subject: str,
	text_content: str,
	to_email: str,
	to_name: str = "",
	html_content: str | None = None,
	allow_smtp_fallback: bool = True,
) -> None:
	"""Send an email using Brevo's transactional API when possible.

	For local development, the helper can fall back to Django's configured email
	backend if no API key is present. Production callers should keep the Brevo API
	key configured and avoid depending on SMTP connectivity.
	"""
	api_key = (getattr(settings, "BREVO_API_KEY", "") or "").strip()
	sender = _parse_sender_address(getattr(settings, "DEFAULT_FROM_EMAIL", ""))

	if api_key:
		payload: dict[str, object] = {
			"sender": {"name": sender.name, "email": sender.email},
			"to": [{"email": to_email, "name": to_name or to_email}],
			"subject": subject,
			"textContent": text_content,
		}
		if html_content:
			payload["htmlContent"] = html_content

		response = requests.post(
			"https://api.brevo.com/v3/smtp/email",
			headers={
				"accept": "application/json",
				"content-type": "application/json",
				"api-key": api_key,
			},
			data=json.dumps(payload),
			timeout=getattr(settings, "EMAIL_TIMEOUT", 10),
		)
		response.raise_for_status()
		return

	if not allow_smtp_fallback:
		raise RuntimeError("BREVO_API_KEY is not configured.")

	send_mail(subject, text_content, settings.DEFAULT_FROM_EMAIL, [to_email], fail_silently=False)
