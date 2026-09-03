# PATH: apps/returns/migrations/0005_complaintmessage.py
# Backend Change Request v2, Part 2 — Item 4 (complaints ticket thread)
#
# NOTE (cross-check, Sep 2026): the ComplaintMessage model was already
# declared in models.py but this migration had never been generated —
# the DB table didn't exist yet even though the model did. Added now.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("returns", "0004_alter_complaint_options_alter_complaint_table"),
    ]

    operations = [
        migrations.CreateModel(
            name="ComplaintMessage",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "sender",
                    models.CharField(
                        choices=[("customer", "Customer"), ("admin", "Admin")],
                        help_text="Who sent this message: customer or admin",
                        max_length=20,
                    ),
                ),
                ("message", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "complaint",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="messages",
                        to="returns.complaint",
                    ),
                ),
                (
                    "sender_user",
                    models.ForeignKey(
                        blank=True,
                        help_text="User who sent this message (admin user or customer's user)",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "complaint_messages",
                "ordering": ["created_at"],
            },
        ),
    ]