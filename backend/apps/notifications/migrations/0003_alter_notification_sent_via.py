# Generated for FIX (B2): sent_via choices realigned to in_app/email/sms

from django.db import migrations, models


def migrate_old_sent_via_values(apps, schema_editor):
    """
    Data migration: existing rows using the old choices ('web',
    'whatsapp') are remapped to the closest new equivalent so no
    existing row is left holding a value outside the new choices list.
      - 'web'      -> 'in_app'  (same concept, renamed)
      - 'whatsapp' -> 'in_app'  (unused in practice - grep confirmed no
                                  code path ever set this - but remapped
                                  defensively in case of manually
                                  inserted/seeded rows)
    """
    Notification = apps.get_model('notifications', 'Notification')
    Notification.objects.filter(sent_via='web').update(sent_via='in_app')
    Notification.objects.filter(sent_via='whatsapp').update(sent_via='in_app')


def reverse_migrate_old_sent_via_values(apps, schema_editor):
    """Reverse: best-effort, maps 'in_app' back to 'web'."""
    Notification = apps.get_model('notifications', 'Notification')
    Notification.objects.filter(sent_via='in_app').update(sent_via='web')


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0002_alter_notification_type_alter_notification_user'),
    ]

    operations = [
        # Data migration runs BEFORE the schema/choices change below,
        # so old values are remapped while the field still accepts them.
        migrations.RunPython(
            migrate_old_sent_via_values,
            reverse_migrate_old_sent_via_values,
        ),
        migrations.AlterField(
            model_name='notification',
            name='sent_via',
            field=models.CharField(
                choices=[('in_app', 'In-App'), ('email', 'Email'), ('sms', 'SMS')],
                default='in_app',
                max_length=20,
            ),
        ),
    ]