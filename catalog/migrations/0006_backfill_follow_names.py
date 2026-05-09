from __future__ import annotations

from django.db import migrations


def backfill_follow_names(apps, schema_editor):
    PersonFollow = apps.get_model("catalog", "PersonFollow")
    CompanyFollow = apps.get_model("catalog", "CompanyFollow")

    for pf in PersonFollow.objects.select_related("person").all().iterator():
        person = getattr(pf, "person", None)
        name = getattr(person, "name", "") or ""
        if (pf.name or "") != name:
            pf.name = name
            pf.save(update_fields=["name"])

    for cf in CompanyFollow.objects.select_related("company").all().iterator():
        company = getattr(cf, "company", None)
        name = getattr(company, "name", "") or ""
        if (cf.name or "") != name:
            cf.name = name
            cf.save(update_fields=["name"])


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0005_companyfollow_name_personfollow_name"),
    ]

    operations = [
        migrations.RunPython(backfill_follow_names, reverse_code=migrations.RunPython.noop),
    ]
