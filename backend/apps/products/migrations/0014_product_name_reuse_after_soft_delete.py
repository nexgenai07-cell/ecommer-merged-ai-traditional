# PATH: apps/products/migrations/0014_product_name_reuse_after_soft_delete.py

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0013_alter_product_sku'),
    ]

    operations = [
        # Drop the old, unconditional unique=True on name — it blocked
        # reusing a soft-deleted product's name forever.
        migrations.AlterField(
            model_name='product',
            name='name',
            field=models.CharField(max_length=255),
        ),
        # Replace it with a partial unique index that only applies to
        # rows where is_delete=False, so a soft-deleted product's name
        # genuinely frees up for a new product to use.
        migrations.AddConstraint(
            model_name='product',
            constraint=models.UniqueConstraint(
                fields=['name'],
                condition=models.Q(is_delete=False),
                name='unique_active_product_name',
            ),
        ),
    ]