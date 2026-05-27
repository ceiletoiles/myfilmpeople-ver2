from django.db import migrations


def backfill_company_followactivity_role(apps, schema_editor):
	FollowActivity = apps.get_model("catalog", "FollowActivity")
	db_alias = schema_editor.connection.alias
	FollowActivity.objects.using(db_alias).filter(entity_type="company").update(role="Studio")


class Migration(migrations.Migration):

	dependencies = [
		("catalog", "0016_followactivity_created_at"),
	]

	operations = [
		migrations.RunPython(backfill_company_followactivity_role, migrations.RunPython.noop),
	]