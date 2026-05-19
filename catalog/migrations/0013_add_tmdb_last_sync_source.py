from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		("catalog", "0012_movie_compound_indexes"),
	]

	operations = [
		migrations.AddField(
			model_name="person",
			name="tmdb_last_sync_source",
			field=models.CharField(blank=True, default="", max_length=20),
		),
		migrations.AddField(
			model_name="company",
			name="tmdb_last_sync_source",
			field=models.CharField(blank=True, default="", max_length=20),
		),
	]
