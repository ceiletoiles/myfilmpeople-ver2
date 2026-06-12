from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

	dependencies = [
		("accounts", "0004_emailverification_verified_via_signup"),
	]

	operations = [
		migrations.CreateModel(
			name="PasswordResetToken",
			fields=[
				("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
				("token_hash", models.CharField(max_length=64, unique=True)),
				("created_at", models.DateTimeField(auto_now_add=True)),
				("expires_at", models.DateTimeField(db_index=True)),
				("used_at", models.DateTimeField(blank=True, db_index=True, null=True)),
				("verification_attempts", models.PositiveIntegerField(default=0)),
				("last_attempt_at", models.DateTimeField(blank=True, null=True)),
				(
					"user",
					models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="password_reset_tokens", to="auth.user"),
				),
			],
			options={
				"ordering": ["-created_at"],
			},
		),
		migrations.CreateModel(
			name="PasswordResetRequestLog",
			fields=[
				("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
				("action", models.CharField(choices=[("request", "Request"), ("verify", "Verify")], max_length=20)),
				("email_hash", models.CharField(blank=True, default="", max_length=64)),
				("ip_hash", models.CharField(blank=True, default="", max_length=64)),
				("success", models.BooleanField(default=False)),
				("rate_limited", models.BooleanField(default=False)),
				("created_at", models.DateTimeField(auto_now_add=True)),
				(
					"user",
					models.ForeignKey(
						blank=True,
						null=True,
						on_delete=django.db.models.deletion.SET_NULL,
						related_name="password_reset_logs",
						to="auth.user",
					),
				),
			],
			options={
				"ordering": ["-created_at"],
			},
		),
		migrations.AddIndex(
			model_name="passwordresettoken",
			index=models.Index(fields=["user", "expires_at"], name="accounts_pr_user_id_2ddca3_idx"),
		),
		migrations.AddIndex(
			model_name="passwordresettoken",
			index=models.Index(fields=["expires_at", "used_at"], name="accounts_pr_expires_47b1f7_idx"),
		),
		migrations.AddIndex(
			model_name="passwordresetrequestlog",
			index=models.Index(fields=["action", "created_at"], name="accounts_pr_action_6f4d3f_idx"),
		),
		migrations.AddIndex(
			model_name="passwordresetrequestlog",
			index=models.Index(fields=["email_hash", "created_at"], name="accounts_pr_email_5a5c36_idx"),
		),
		migrations.AddIndex(
			model_name="passwordresetrequestlog",
			index=models.Index(fields=["ip_hash", "created_at"], name="accounts_pr_ip_has_3a71aa_idx"),
		),
	]
