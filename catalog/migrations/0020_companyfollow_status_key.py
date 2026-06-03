from django.db import migrations, models


def forwards(apps, schema_editor):
	company_follow = apps.get_model("catalog", "CompanyFollow")
	table_name = company_follow._meta.db_table
	with schema_editor.connection.cursor() as cursor:
		existing_columns = {column.name for column in schema_editor.connection.introspection.get_table_description(cursor, table_name)}
	if "status_key" in existing_columns:
		return
	field = models.CharField(blank=True, default="", max_length=20)
	field.set_attributes_from_name("status_key")
	schema_editor.add_field(company_follow, field)


class Migration(migrations.Migration):
	dependencies = [
		("catalog", "0019_companyfollow_status"),
	]

	operations = [
		migrations.SeparateDatabaseAndState(
			database_operations=[migrations.RunPython(forwards, migrations.RunPython.noop)],
			state_operations=[
				migrations.AddField(
					model_name="companyfollow",
					name="status_key",
					field=models.CharField(blank=True, default="", max_length=20),
				),
			],
		),
	]
