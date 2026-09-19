# PATH: apps/returns/migrations/0007_return_status_pending_only.py
#
# Return statuses are now exactly: pending, approved, rejected.
#   - old "requested" rows  -> "pending"
#   - old "completed" rows  -> "approved" (a completed return had already
#     been approved, so it stays a final, resolved decision)

from django.db import migrations, models


def forwards(apps, schema_editor):
    Return = apps.get_model("returns", "Return")
    # .update() on purpose: skips Return.save()/full_clean(), which would
    # otherwise reject the old status values.
    Return.objects.filter(status="requested").update(status="pending")
    Return.objects.filter(status="completed").update(status="approved")


def backwards(apps, schema_editor):
    Return = apps.get_model("returns", "Return")
    Return.objects.filter(status="pending").update(status="requested")
    # "completed" rows were merged into "approved" and can't be told apart
    # again, so they simply stay "approved" when rolling back.


class Migration(migrations.Migration):

    dependencies = [
        ("returns", "0006_remove_complaintmessage_sender_user_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name="return",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="return",
            name="resolved_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Time the return was approved or rejected.",
                null=True,
            ),
        ),
    ]