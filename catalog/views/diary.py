from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from ..forms import DiaryAccountForm
from ..models import DiaryAccount


def _get_diary_account(user) -> DiaryAccount:
	account, _ = DiaryAccount.objects.get_or_create(user=user)
	return account


def _format_last_sync_label(account: DiaryAccount) -> str:
	if account.last_successful_sync_at is None:
		return "No sync yet"
	local_dt = timezone.localtime(account.last_successful_sync_at)
	day = local_dt.day
	hour = local_dt.hour % 12 or 12
	minute = local_dt.minute
	ampm = "AM" if local_dt.hour < 12 else "PM"
	return f"{local_dt.strftime('%b')} {day}, {local_dt.year} at {hour}:{minute:02d} {ampm}"


@login_required
def diary(request: HttpRequest) -> HttpResponse:
	account = _get_diary_account(request.user)
	form = DiaryAccountForm(initial={"letterboxd_username": account.letterboxd_username})

	has_letterboxd_account = bool(account.letterboxd_username.strip())
	last_sync_label = _format_last_sync_label(account)
	connection_status = "Connected" if has_letterboxd_account else "Not connected"
	rss_feed_url = (
		f"https://letterboxd.com/{account.letterboxd_username.strip()}/rss/"
		if has_letterboxd_account
		else ""
	)

	return render(
		request,
		"catalog/diary.html",
		{
			"form": form,
			"letterboxd_username": account.letterboxd_username.strip(),
			"connection_status": connection_status,
			"last_sync_label": last_sync_label,
			"has_letterboxd_account": has_letterboxd_account,
			"rss_feed_url": rss_feed_url,
			"diary_account": account,
		},
	)


@login_required
def diary_settings(request: HttpRequest) -> HttpResponse:
	if request.method != "POST":
		return redirect("diary")

	account = _get_diary_account(request.user)
	form = DiaryAccountForm(request.POST)
	if not form.is_valid():
		messages.error(request, "Check the Letterboxd username and try again.")
		return render(
			request,
			"catalog/diary.html",
			{
				"form": form,
				"letterboxd_username": account.letterboxd_username.strip(),
				"connection_status": "Connected" if account.letterboxd_username.strip() else "Not connected",
				"last_sync_label": _format_last_sync_label(account),
				"has_letterboxd_account": bool(account.letterboxd_username.strip()),
				"rss_feed_url": (
					f"https://letterboxd.com/{account.letterboxd_username.strip()}/rss/"
					if account.letterboxd_username.strip()
					else ""
				),
				"diary_account": account,
			},
			status=400,
		)

	new_username = form.cleaned_data["letterboxd_username"]
	if new_username != account.letterboxd_username:
		account.letterboxd_username = new_username
		account.save(update_fields=["letterboxd_username", "updated_at"])
		messages.success(request, "Letterboxd username saved.")
	else:
		messages.info(request, "Letterboxd username is unchanged.")
	return redirect("diary")
