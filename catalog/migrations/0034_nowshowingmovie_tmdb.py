from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0033_nowshowingmovie"),
    ]

    operations = [
        migrations.AddField(
            model_name="nowshowingmovie",
            name="tmdb_id",
            field=models.PositiveIntegerField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="nowshowingmovie",
            name="tmdb_poster_path",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
