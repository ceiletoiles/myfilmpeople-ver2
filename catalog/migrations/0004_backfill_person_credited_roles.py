from __future__ import annotations

from django.db import migrations


def backfill_person_credited_roles(apps, schema_editor):
    Person = apps.get_model("catalog", "Person")

    def extract_roles(credits: dict) -> list[str]:
        roles: set[str] = set()
        cast_items = credits.get("cast", []) or []
        crew_items = credits.get("crew", []) or []

        if len(cast_items) > 0:
            roles.add("Actor")

        for item in crew_items:
            job = (item.get("job") or "").strip()
            if job:
                roles.add(job)

        preferred = {"director": 0, "actor": 1}
        return sorted(roles, key=lambda r: (preferred.get(r.strip().lower(), 99), r.lower()))

    for person in Person.objects.all().iterator():
        tmdb_raw = person.tmdb_raw if isinstance(person.tmdb_raw, dict) else {}
        if isinstance(tmdb_raw.get("credited_roles"), list):
            continue
        credits = person.tmdb_credits_raw if isinstance(person.tmdb_credits_raw, dict) else {}
        if not credits:
            continue

        tmdb_raw = {**tmdb_raw, "credited_roles": extract_roles(credits)}
        person.tmdb_raw = tmdb_raw
        person.save(update_fields=["tmdb_raw"])


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0003_normalize_personfollow_role_values"),
    ]

    operations = [
        migrations.RunPython(backfill_person_credited_roles, reverse_code=migrations.RunPython.noop),
    ]
