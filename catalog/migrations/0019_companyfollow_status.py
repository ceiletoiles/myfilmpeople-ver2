from django.db import migrations, models


ADD_STATUS_SQL = """
SET @col_exists := (
  SELECT COUNT(*)
  FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'catalog_companyfollow'
    AND COLUMN_NAME = 'status'
);
SET @sql := IF(
  @col_exists = 0,
  'ALTER TABLE `catalog_companyfollow` ADD COLUMN `status` varchar(20) NOT NULL DEFAULT ''''',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
"""

DROP_STATUS_SQL = """
SET @col_exists := (
  SELECT COUNT(*)
  FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'catalog_companyfollow'
    AND COLUMN_NAME = 'status'
);
SET @sql := IF(
  @col_exists = 1,
  'ALTER TABLE `catalog_companyfollow` DROP COLUMN `status`',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
"""


class Migration(migrations.Migration):
	dependencies = [
		("catalog", "0018_rename_catalog_foll_user_c3d0f8_idx_catalog_fol_user_id_55c3f3_idx_and_more"),
	]

	operations = [
		migrations.SeparateDatabaseAndState(
			database_operations=[
				migrations.RunSQL(ADD_STATUS_SQL, reverse_sql=DROP_STATUS_SQL),
			],
			state_operations=[
				migrations.AddField(
					model_name="companyfollow",
					name="status",
					field=models.CharField(blank=True, default="", max_length=20),
				),
			],
		),
	]
