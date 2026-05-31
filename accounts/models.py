
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
