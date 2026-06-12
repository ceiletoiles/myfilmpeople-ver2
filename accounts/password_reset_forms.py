from __future__ import annotations

from django import forms
from django.contrib.auth.forms import SetPasswordForm


class PasswordResetRequestForm(forms.Form):
	email = forms.EmailField(label="Email address")

	def clean_email(self):
		return (self.cleaned_data.get("email") or "").strip().lower()


class PasswordResetConfirmForm(SetPasswordForm):
	def __init__(self, user, *args, **kwargs):
		super().__init__(user, *args, **kwargs)
		self.fields["new_password1"].label = "New password"
		self.fields["new_password2"].label = "Confirm password"
		self.fields["new_password1"].help_text = ""
		self.fields["new_password2"].help_text = ""
