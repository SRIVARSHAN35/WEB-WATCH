from django.db import migrations, models


def mark_legacy_meta_acceptances(apps, schema_editor):
    Delivery = apps.get_model("monitor", "WhatsAppDelivery")
    Delivery.objects.filter(status="sent").update(status="accepted")


def restore_legacy_status(apps, schema_editor):
    Delivery = apps.get_model("monitor", "WhatsAppDelivery")
    Delivery.objects.filter(status="accepted").update(status="sent")


class Migration(migrations.Migration):

    dependencies = [
        ("monitor", "0020_whatsappdelivery_next_attempt_at"),
    ]

    operations = [
        migrations.RunPython(mark_legacy_meta_acceptances, restore_legacy_status),
        migrations.RenameField(
            model_name="whatsappdelivery",
            old_name="sent_at",
            new_name="accepted_at",
        ),
        migrations.AlterField(
            model_name="whatsappdelivery",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("sending", "Sending"),
                    ("accepted", "Accepted by Meta; delivery unconfirmed"),
                    ("failed", "Failed"),
                ],
                default="queued",
                max_length=12,
            ),
        ),
    ]
