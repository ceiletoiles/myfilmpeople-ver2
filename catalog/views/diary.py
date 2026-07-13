from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


@login_required
def diary(request: HttpRequest) -> HttpResponse:
	context = {
		"letterboxd_username": "",
		"connection_status": "Not connected",
		"last_sync_label": "No sync yet",
		"has_letterboxd_account": False,
	}

	return render(
		request,
		"catalog/diary.html",
		context,
	)
