from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		("catalog", "0030_diaryentry_backdrop_path"),
	]

	operations = [
		migrations.AddField(
			model_name="personfollow",
			name="favorite",
			field=models.BooleanField(default=False),
		),
		migrations.AddField(
			model_name="companyfollow",
			name="favorite",
			field=models.BooleanField(default=False),
		),
	]
