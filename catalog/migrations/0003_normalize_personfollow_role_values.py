from __future__ import annotations

from django.db import IntegrityError, migrations


def normalize_personfollow_roles(apps, schema_editor):
    PersonFollow = apps.get_model("catalog", "PersonFollow")

    mapping = {
        "director": "Director",
        "actor": "Actor",
        "crew": "Crew",
    }

    for old, new in mapping.items():
        for pf in PersonFollow.objects.filter(role=old).iterator():
            pf.role = new
            try:
                pf.save(update_fields=["role"])
            except IntegrityError:
                # If a row already exists for the new role, keep it and drop the old one.
                pf.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_alter_personfollow_role"),
    ]

    operations = [
        migrations.RunPython(normalize_personfollow_roles, reverse_code=migrations.RunPython.noop),
    ]
