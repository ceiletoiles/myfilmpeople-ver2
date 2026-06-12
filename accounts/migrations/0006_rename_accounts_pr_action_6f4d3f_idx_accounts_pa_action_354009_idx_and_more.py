from django.db import migrations


class Migration(migrations.Migration):

	dependencies = [
		("accounts", "0005_passwordresettoken_passwordresetrequestlog"),
	]

	operations = [
		migrations.RenameIndex(
			model_name="passwordresetrequestlog",
			old_name="accounts_pr_action_6f4d3f_idx",
			new_name="accounts_pa_action_354009_idx",
		),
		migrations.RenameIndex(
			model_name="passwordresetrequestlog",
			old_name="accounts_pr_email_5a5c36_idx",
			new_name="accounts_pa_email_h_2da129_idx",
		),
		migrations.RenameIndex(
			model_name="passwordresetrequestlog",
			old_name="accounts_pr_ip_has_3a71aa_idx",
			new_name="accounts_pa_ip_hash_4420d3_idx",
		),
		migrations.RenameIndex(
			model_name="passwordresettoken",
			old_name="accounts_pr_user_id_2ddca3_idx",
			new_name="accounts_pa_user_id_e5b29b_idx",
		),
		migrations.RenameIndex(
			model_name="passwordresettoken",
			old_name="accounts_pr_expires_47b1f7_idx",
			new_name="accounts_pa_expires_4f8a3c_idx",
		),
	]
