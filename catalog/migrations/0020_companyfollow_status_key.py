from django.db import migrations, models


ADD_STATUS_KEY_SQL = """
SET @col_exists := (
  SELECT COUNT(*)
  FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'catalog_companyfollow'
    AND COLUMN_NAME = 'status_key'
);
SET @sql := IF(
  @col_exists = 0,
  'ALTER TABLE `catalog_companyfollow` ADD COLUMN `status_key` varchar(20) NOT NULL DEFAULT '''' AFTER `status`',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
"""

DROP_STATUS_KEY_SQL = """
SET @col_exists := (
  SELECT COUNT(*)
  FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'catalog_companyfollow'
    AND COLUMN_NAME = 'status_key'
);
SET @sql := IF(
  @col_exists = 1,
  'ALTER TABLE `catalog_companyfollow` DROP COLUMN `status_key`',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
"""


class Migration(migrations.Migration):
	dependencies = [
		("catalog", "0019_companyfollow_status"),
	]

	operations = [
		migrations.SeparateDatabaseAndState(
			database_operations=[
				migrations.RunSQL(ADD_STATUS_KEY_SQL, reverse_sql=DROP_STATUS_KEY_SQL),
			],
			state_operations=[
				migrations.AddField(
					model_name="companyfollow",
					name="status_key",
					field=models.CharField(blank=True, default="", max_length=20),
				),
			],
		),
	]
