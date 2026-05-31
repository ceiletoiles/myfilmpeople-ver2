from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from catalog.models import CompanyFollow, PersonFollow


@receiver(post_save, sender=PersonFollow)
def personfollow_post_save(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        # Lazy import to avoid circular imports with views
        from .models import BadgeNotification
        from .views import FOLLOW_BADGE_LEVELS

        user = instance.user
        person_count = PersonFollow.objects.filter(user=user).count()
        company_count = CompanyFollow.objects.filter(user=user).count()
        follow_count = int(person_count + company_count)
        badge = next((b for b in FOLLOW_BADGE_LEVELS if follow_count >= int(b["min_count"])), None)
        if badge:
            level = int(badge.get("level", 0))
            # Only skip creating if an unseen notification for this level already exists.
            if not BadgeNotification.objects.filter(user=user, level=level, seen=False).exists():
                BadgeNotification.objects.create(
                    user=user,
                    level=level,
                    min_count=int(badge.get("min_count", 0)),
                    label=badge.get("label", ""),
                    image=badge.get("image", ""),
                )
    except Exception:
        # best-effort: don't raise
        pass


@receiver(post_save, sender=CompanyFollow)
def companyfollow_post_save(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        from .models import BadgeNotification
        from .views import FOLLOW_BADGE_LEVELS

        user = instance.user
        person_count = PersonFollow.objects.filter(user=user).count()
        company_count = CompanyFollow.objects.filter(user=user).count()
        follow_count = int(person_count + company_count)
        badge = next((b for b in FOLLOW_BADGE_LEVELS if follow_count >= int(b["min_count"])), None)
        if badge:
            level = int(badge.get("level", 0))
            # Only skip creating if an unseen notification for this level already exists.
            if not BadgeNotification.objects.filter(user=user, level=level, seen=False).exists():
                BadgeNotification.objects.create(
                    user=user,
                    level=level,
                    min_count=int(badge.get("min_count", 0)),
                    label=badge.get("label", ""),
                    image=badge.get("image", ""),
                )
    except Exception:
        pass
