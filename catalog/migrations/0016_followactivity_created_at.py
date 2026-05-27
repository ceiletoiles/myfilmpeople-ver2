from django.db import migrations, models
from django.utils import timezone


def backfill_followactivity_created_at(apps, schema_editor):
	PersonFollow = apps.get_model("catalog", "PersonFollow")
	CompanyFollow = apps.get_model("catalog", "CompanyFollow")
	FollowActivity = apps.get_model("catalog", "FollowActivity")
	db_alias = schema_editor.connection.alias

	person_created_at = {}
	for follow in PersonFollow.objects.using(db_alias).only("user_id", "person_id", "role", "created_at").iterator():
		person_created_at[(follow.user_id, follow.person_id, follow.role)] = follow.created_at

	company_created_at = {}
	for follow in CompanyFollow.objects.using(db_alias).only("user_id", "company_id", "created_at").iterator():
		company_created_at[(follow.user_id, follow.company_id)] = follow.created_at

	for activity in FollowActivity.objects.using(db_alias).filter(action="follow").iterator():
		new_created_at = None
		if activity.entity_type == "person" and activity.person_id is not None:
			new_created_at = person_created_at.get((activity.user_id, activity.person_id, activity.role or ""))
		elif activity.entity_type == "company" and activity.company_id is not None:
			new_created_at = company_created_at.get((activity.user_id, activity.company_id))

		if new_created_at and activity.created_at != new_created_at:
			activity.created_at = new_created_at
			activity.save(update_fields=["created_at"])


class Migration(migrations.Migration):

	dependencies = [
		("catalog", "0015_follow_activity"),
	]

	operations = [
		migrations.AlterField(
			model_name="followactivity",
			name="created_at",
			field=models.DateTimeField(default=timezone.now, db_index=True),
		),
		migrations.RunPython(backfill_followactivity_created_at, migrations.RunPython.noop),
	]