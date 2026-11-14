from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0032_diaryaccount_suppressed_rss_items"),
    ]

    operations = [
        migrations.CreateModel(
            name="NowShowingMovie",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("city", models.CharField(db_index=True, max_length=120)),
                ("bookmyshow_url", models.URLField(max_length=500)),
                ("title", models.CharField(max_length=255)),
                ("poster_url", models.URLField(blank=True, max_length=1000)),
                ("languages", models.JSONField(blank=True, default=list)),
                ("genres", models.JSONField(blank=True, default=list)),
                ("rating", models.CharField(blank=True, max_length=30)),
                ("release_date", models.DateField(blank=True, null=True)),
                ("runtime_minutes", models.PositiveIntegerField(blank=True, null=True)),
                ("director", models.CharField(blank=True, max_length=255)),
                ("cast", models.JSONField(blank=True, default=list)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("last_seen_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["title", "id"]},
        ),
        migrations.AddConstraint(
            model_name="nowshowingmovie",
            constraint=models.UniqueConstraint(fields=("city", "bookmyshow_url"), name="uniq_now_showing_city_url"),
        ),
    ]
