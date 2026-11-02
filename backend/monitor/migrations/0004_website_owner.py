from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import migrations, models
import django.db.models.deletion


def assign_legacy_websites(apps, schema_editor):
    Website = apps.get_model("monitor", "Website")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    database = schema_editor.connection.alias

    if not Website.objects.using(database).filter(owner__isnull=True).exists():
        return

    username = "webwatch-legacy"
    suffix = 1
    while User.objects.using(database).filter(username=username).exists():
        username = f"webwatch-legacy-{suffix}"
        suffix += 1

    legacy_user = User(
        username=username,
        email="",
        is_active=False,
    )
    legacy_user.password = make_password(None)
    legacy_user.save(using=database)
    Website.objects.using(database).filter(owner__isnull=True).update(
        owner_id=legacy_user.pk
    )


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("monitor", "0003_websitechange"),
    ]

    operations = [
        migrations.AddField(
            model_name="website",
            name="owner",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="websites",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(assign_legacy_websites, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="website",
            name="owner",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="websites",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]