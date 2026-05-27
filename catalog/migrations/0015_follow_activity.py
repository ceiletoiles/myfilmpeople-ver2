from django.conf import settings
from django.db import migrations, models
from django.db.models import deletion


def backfill_follow_activities(apps, schema_editor):
	PersonFollow = apps.get_model("catalog", "PersonFollow")
	CompanyFollow = apps.get_model("catalog", "CompanyFollow")
	FollowActivity = apps.get_model("catalog", "FollowActivity")
	db_alias = schema_editor.connection.alias
	activities = []

	for follow in PersonFollow.objects.using(db_alias).select_related("person").iterator():
		person = follow.person
		activities.append(
			FollowActivity(
				user_id=follow.user_id,
				entity_type="person",
				action="follow",
				person_id=follow.person_id,
				entity_name=(follow.name or getattr(person, "name", "") or ""),
				role=follow.role or "",
				image_path=getattr(person, "profile_path", "") or "",
				created_at=follow.created_at,
				updated_at=follow.updated_at,
			)
		)

	for follow in CompanyFollow.objects.using(db_alias).select_related("company").iterator():
		company = follow.company
		activities.append(
			FollowActivity(
				user_id=follow.user_id,
				entity_type="company",
				action="follow",
				company_id=follow.company_id,
				entity_name=(follow.name or getattr(company, "name", "") or ""),
				image_path=getattr(company, "logo_path", "") or "",
				created_at=follow.created_at,
				updated_at=follow.updated_at,
			)
		)

	if activities:
		FollowActivity.objects.using(db_alias).bulk_create(activities, batch_size=500)


class Migration(migrations.Migration):

	dependencies = [
		("catalog", "0014_rename_movie_title_tmdbid_idx_catalog_mov_title_313ee4_idx_and_more"),
		migrations.swappable_dependency(settings.AUTH_USER_MODEL),
	]

	operations = [
		migrations.CreateModel(
			name="FollowActivity",
			fields=[
				(
					"id",
					models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
				),
				("entity_type", models.CharField(choices=[("person", "Person"), ("company", "Company")], max_length=20)),
				("action", models.CharField(choices=[("follow", "Followed"), ("unfollow", "Unfollowed")], max_length=20)),
				("entity_name", models.CharField(max_length=255)),
				("role", models.CharField(blank=True, max_length=100)),
				("image_path", models.CharField(blank=True, max_length=255)),
				("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
				("updated_at", models.DateTimeField(auto_now=True)),
				(
					"company",
					models.ForeignKey(blank=True, null=True, on_delete=deletion.SET_NULL, related_name="follow_activities", to="catalog.company"),
				),
				(
					"person",
					models.ForeignKey(blank=True, null=True, on_delete=deletion.SET_NULL, related_name="follow_activities", to="catalog.person"),
				),
				(
					"user",
					models.ForeignKey(on_delete=deletion.CASCADE, to=settings.AUTH_USER_MODEL),
				),
			],
			options={
				"ordering": ["-created_at", "-id"],
			},
		),
		migrations.AddIndex(
			model_name="followactivity",
			index=models.Index(fields=["user", "created_at"], name="catalog_foll_user_c3d0f8_idx"),
		),
		migrations.RunPython(backfill_follow_activities, migrations.RunPython.noop),
	]