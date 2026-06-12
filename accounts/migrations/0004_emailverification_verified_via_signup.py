from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_emailverification"),
    ]

    operations = [
        migrations.AddField(
            model_name="emailverification",
            name="verified_via_signup",
            field=models.BooleanField(
                default=False,
                help_text="True when the account was created through the email-OTP signup flow.",
            ),
        ),
    ]
