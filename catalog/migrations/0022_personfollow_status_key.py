from django.db import migrations, models


APPLY_STATUS_KEY_SQL = """
SET @col_exists := (
  SELECT COUNT(*)
  FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'catalog_personfollow'
    AND COLUMN_NAME = 'status_key'
);
SET @sql := IF(
  @col_exists = 1,
  'ALTER TABLE `catalog_personfollow` MODIFY COLUMN `status_key` varchar(20) NOT NULL DEFAULT ''''',
  'ALTER TABLE `catalog_personfollow` ADD COLUMN `status_key` varchar(20) NOT NULL DEFAULT '''' AFTER `status`'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
"""

REVERSE_STATUS_KEY_SQL = """
SET @col_exists := (
  SELECT COUNT(*)
  FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'catalog_personfollow'
    AND COLUMN_NAME = 'status_key'
);
SET @sql := IF(
  @col_exists = 1,
  'ALTER TABLE `catalog_personfollow` DROP COLUMN `status_key`',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
"""


class Migration(migrations.Migration):
	dependencies = [
		("catalog", "0021_personfollow_status"),
	]

	operations = [
		migrations.SeparateDatabaseAndState(
			database_operations=[
				migrations.RunSQL(APPLY_STATUS_KEY_SQL, reverse_sql=REVERSE_STATUS_KEY_SQL),
			],
			state_operations=[
				migrations.AddField(
					model_name="personfollow",
					name="status_key",
					field=models.CharField(blank=True, default="", max_length=20),
				),
			],
		),
	]
