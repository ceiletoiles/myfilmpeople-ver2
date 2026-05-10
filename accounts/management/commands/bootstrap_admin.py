from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create or update an admin user from environment variables."

    def handle(self, *args, **options):
        User = get_user_model()

        username_field = User.USERNAME_FIELD
        username = (self._get_env("DJANGO_ADMIN_USERNAME") or "").strip()
        email = (self._get_env("DJANGO_ADMIN_EMAIL") or "").strip()
        password = self._get_env("DJANGO_ADMIN_PASSWORD") or ""

        if not username or not password:
            self.stdout.write("Skipping admin bootstrap: DJANGO_ADMIN_USERNAME and/or DJANGO_ADMIN_PASSWORD is not set.")
            return

        lookup = {username_field: username}
        user, created = User.objects.get_or_create(defaults={"email": email} if email else {}, **lookup)

        changed = False
        if email and getattr(user, "email", "") != email:
            user.email = email
            changed = True

        if not user.is_staff:
            user.is_staff = True
            changed = True

        if not user.is_superuser:
            user.is_superuser = True
            changed = True

        if password:
            user.set_password(password)
            changed = True

        if changed or created:
            user.save()

        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} admin user '{username}'."))

    @staticmethod
    def _get_env(name: str):
        import os

        return os.getenv(name)