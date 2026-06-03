from django.db import migrations, models


def _table_columns(schema_editor, table_name: str) -> set[str]:
	with schema_editor.connection.cursor() as cursor:
		return {column.name for column in schema_editor.connection.introspection.get_table_description(cursor, table_name)}


def _add_char_field_if_missing(apps, schema_editor, model_name: str, field_name: str) -> None:
	model = apps.get_model("catalog", model_name)
	table_name = model._meta.db_table
	if field_name in _table_columns(schema_editor, table_name):
		return
	field = models.CharField(blank=True, default="", max_length=20)
	field.set_attributes_from_name(field_name)
	schema_editor.add_field(model, field)


def forwards(apps, schema_editor):
	for model_name in ("CompanyFollow", "PersonFollow"):
		for field_name in ("status", "status_key"):
			_add_char_field_if_missing(apps, schema_editor, model_name, field_name)


class Migration(migrations.Migration):
	dependencies = [
		("catalog", "0022_personfollow_status_key"),
	]

	operations = [
		migrations.RunPython(forwards, migrations.RunPython.noop),
	]
