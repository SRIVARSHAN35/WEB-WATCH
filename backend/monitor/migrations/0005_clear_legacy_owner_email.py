from django.db import migrations


def clear_placeholder_emails(apps, schema_editor):
    User = apps.get_model("auth", "User")
    database = schema_editor.connection.alias
    User.objects.using(database).filter(
        username__startswith="webwatch-legacy",
        is_active=False,
        email__endswith="@local.invalid",
    ).update(email="")


class Migration(migrations.Migration):
    dependencies = [
        ("monitor", "0004_website_owner"),
    ]

    operations = [
        migrations.RunPython(clear_placeholder_emails, migrations.RunPython.noop),
    ]