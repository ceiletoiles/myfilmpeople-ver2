from __future__ import annotations

import re

from django import forms
from django.core.exceptions import ValidationError


_LETTERBOXD_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class DiaryAccountForm(forms.Form):
	letterboxd_username = forms.CharField(
		max_length=80,
		label="Letterboxd Username",
		help_text="Enter your Letterboxd username without the @ symbol.",
		widget=forms.TextInput(
			attrs={
				"autocomplete": "off",
				"autocapitalize": "off",
				"spellcheck": "false",
				"placeholder": "@username",
			}
		),
	)

	def clean_letterboxd_username(self) -> str:
		username = (self.cleaned_data.get("letterboxd_username") or "").strip()
		username = username.lstrip("@").strip()
		if not username:
			raise ValidationError("Enter your Letterboxd username.")
		if not _LETTERBOXD_USERNAME_RE.fullmatch(username):
			raise ValidationError("Use only letters, numbers, underscores, or hyphens.")
		return username


class DiaryImportForm(forms.Form):
	import_file = forms.FileField(
		label="Letterboxd export file",
		help_text="Upload your Letterboxd diary CSV or ZIP export.",
		widget=forms.ClearableFileInput(
			attrs={
				"accept": ".csv,.zip",
			}
		),
	)
