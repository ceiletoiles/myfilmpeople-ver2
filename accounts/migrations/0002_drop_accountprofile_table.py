from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        # Some older deployments left a manual `accounts_accountprofile` table
        # behind that references auth_user without the Django model. That can
        # block deleting users in the admin due to a DB-level FK. Drop the
        # table if it exists. This is safe because the project no longer
        # defines that model.
        migrations.RunSQL(
            sql="""
            DROP TABLE IF EXISTS accounts_accountprofile;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
