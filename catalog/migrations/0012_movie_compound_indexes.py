from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0011_add_movie_indexes"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="movie",
            index=models.Index(fields=["title", "tmdb_id"], name="movie_title_tmdbid_idx"),
        ),
        migrations.AddIndex(
            model_name="movie",
            index=models.Index(fields=["release_date", "tmdb_id"], name="movie_rel_dt_tmdbid_idx"),
        ),
        migrations.AddIndex(
            model_name="movie",
            index=models.Index(fields=["tmdb_last_sync_at", "tmdb_id"], name="movie_tmdbsync_tmdbid_idx"),
        ),
    ]
