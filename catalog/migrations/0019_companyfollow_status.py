from django.db import migrations, models


def forwards(apps, schema_editor):
	company_follow = apps.get_model("catalog", "CompanyFollow")
	table_name = company_follow._meta.db_table
	with schema_editor.connection.cursor() as cursor:
		existing_columns = {column.name for column in schema_editor.connection.introspection.get_table_description(cursor, table_name)}
	if "status" in existing_columns:
		return
	field = models.CharField(blank=True, default="", max_length=20)
	field.set_attributes_from_name("status")
	schema_editor.add_field(company_follow, field)


class Migration(migrations.Migration):
	dependencies = [
		("catalog", "0018_rename_catalog_foll_user_c3d0f8_idx_catalog_fol_user_id_55c3f3_idx_and_more"),
	]

	operations = [
		migrations.SeparateDatabaseAndState(
			database_operations=[migrations.RunPython(forwards, migrations.RunPython.noop)],
			state_operations=[
				migrations.AddField(
					model_name="companyfollow",
					name="status",
					field=models.CharField(blank=True, default="", max_length=20),
				),
			],
		),
	]
