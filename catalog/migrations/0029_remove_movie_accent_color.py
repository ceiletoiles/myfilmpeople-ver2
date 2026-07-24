from django.db import migrations


class Migration(migrations.Migration):
	dependencies = [
		("catalog", "0028_diaryentry_accent_color"),
	]

	operations = [
		migrations.RemoveField(
			model_name="movie",
			name="accent_color",
		),
	]
