# PATH: apps/products/migrations/0014_product_sku_reuse_after_soft_delete.py

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0014_product_name_reuse_after_soft_delete'),
    ]

    operations = [
        # Drop the old, unconditional unique=True on sku — it blocked
        # reusing a soft-deleted product's SKU forever.
        migrations.AlterField(
            model_name='product',
            name='sku',
            field=models.CharField(blank=True, max_length=15),
        ),
        # Replace it with a partial unique index that only applies to
        # rows where is_delete=False, so a soft-deleted product's SKU
        # genuinely frees up for a new product to use.
        migrations.AddConstraint(
            model_name='product',
            constraint=models.UniqueConstraint(
                fields=['sku'],
                condition=models.Q(is_delete=False),
                name='unique_active_product_sku',
            ),
        ),
    ]