from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0010_newmoviearrival_seen_at"),
    ]

    operations = [
        migrations.AlterField(
            model_name="movie",
            name="title",
            field=models.CharField(max_length=255, db_index=True),
        ),
        migrations.AlterField(
            model_name="movie",
            name="release_date",
            field=models.DateField(null=True, blank=True, db_index=True),
        ),
        migrations.AlterField(
            model_name="movie",
            name="tmdb_last_sync_at",
            field=models.DateTimeField(null=True, blank=True, db_index=True),
        ),
    ]
