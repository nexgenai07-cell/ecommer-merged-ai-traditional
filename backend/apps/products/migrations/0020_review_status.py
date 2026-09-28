# Generated manually — adds the review-moderation `status` field
# (pending / approved / rejected) and backfills existing rows so
# nothing currently visible on the site disappears the moment this ships.

from django.db import migrations, models


def backfill_review_status(apps, schema_editor):
    """
    Preserve current site behaviour on deploy:
      - a review that was already visible (is_active=True) becomes
        'approved', so it doesn't vanish from the product page.
      - a review an admin had previously hidden (is_active=False)
        becomes 'rejected' rather than 'pending', so it doesn't
        unexpectedly show up in the new pending-review queue.
    """
    Review = apps.get_model('products', 'Review')
    Review.objects.filter(is_active=True).update(status='approved')
    Review.objects.filter(is_active=False).update(status='rejected')


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0019_product_purchase_price'),
    ]

    operations = [
        migrations.AddField(
            model_name='review',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('approved', 'Approved'),
                    ('rejected', 'Rejected'),
                ],
                default='pending',
                max_length=10,
                help_text=(
                    "pending = awaiting admin review (default for every "
                    "customer-submitted review); approved = visible on "
                    "the product page; rejected = hidden, admin declined it."
                ),
            ),
        ),
        migrations.RunPython(backfill_review_status, migrations.RunPython.noop),
    ]