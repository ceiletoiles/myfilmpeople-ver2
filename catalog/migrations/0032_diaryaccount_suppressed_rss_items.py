from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		("catalog", "0031_follow_favorite"),
	]

	operations = [
		migrations.AddField(
			model_name="diaryaccount",
			name="suppressed_rss_items",
			field=models.JSONField(blank=True, default=list),
		),
	]