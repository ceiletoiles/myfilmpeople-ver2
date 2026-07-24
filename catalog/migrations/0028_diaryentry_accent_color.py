from django.db import migrations, models


class Migration(migrations.Migration):
	dependencies = [
		("catalog", "0027_movie_accent_color"),
	]

	operations = [
		migrations.AddField(
			model_name="diaryentry",
			name="accent_color",
			field=models.CharField(blank=True, max_length=7, null=True),
		),
	]
