from django.db import migrations, models


APPLY_STATUS_SQL = """
SET @col_exists := (
  SELECT COUNT(*)
  FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'catalog_personfollow'
    AND COLUMN_NAME = 'status'
);
SET @sql := IF(
  @col_exists = 1,
  'ALTER TABLE `catalog_personfollow` MODIFY COLUMN `status` varchar(20) NOT NULL DEFAULT ''''',
  'ALTER TABLE `catalog_personfollow` ADD COLUMN `status` varchar(20) NOT NULL DEFAULT '''' AFTER `name`'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
"""

REVERSE_STATUS_SQL = """
SET @col_exists := (
  SELECT COUNT(*)
  FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'catalog_personfollow'
    AND COLUMN_NAME = 'status'
);
SET @sql := IF(
  @col_exists = 1,
  'ALTER TABLE `catalog_personfollow` DROP COLUMN `status`',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
"""


class Migration(migrations.Migration):
	dependencies = [
		("catalog", "0020_companyfollow_status_key"),
	]

	operations = [
		migrations.SeparateDatabaseAndState(
			database_operations=[
				migrations.RunSQL(APPLY_STATUS_SQL, reverse_sql=REVERSE_STATUS_SQL),
			],
			state_operations=[
				migrations.AddField(
					model_name="personfollow",
					name="status",
					field=models.CharField(blank=True, default="", max_length=20),
				),
			],
		),
	]
