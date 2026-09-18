# PATH: apps/products/migrations/0018_discount_code_reuse_after_soft_delete.py

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0017_review'),
    ]

    operations = [
        # Drop the old, unconditional unique=True on code — it blocked
        # reusing a soft-deleted discount's code forever.
        migrations.AlterField(
            model_name='discount',
            name='code',
            field=models.CharField(max_length=50),
        ),
        # Replace it with a partial unique index that only applies to
        # rows where is_delete=False, so a soft-deleted discount's code
        # genuinely frees up for a new discount to use.
        migrations.AddConstraint(
            model_name='discount',
            constraint=models.UniqueConstraint(
                fields=['code'],
                condition=models.Q(is_delete=False),
                name='unique_active_discount_code',
            ),
        ),
    ]