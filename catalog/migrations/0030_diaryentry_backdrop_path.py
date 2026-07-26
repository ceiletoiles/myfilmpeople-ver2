from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		("catalog", "0029_remove_movie_accent_color"),
	]

	operations = [
		migrations.AddField(
			model_name="diaryentry",
			name="backdrop_path",
			field=models.CharField(blank=True, default="", max_length=255),
		),
	]
