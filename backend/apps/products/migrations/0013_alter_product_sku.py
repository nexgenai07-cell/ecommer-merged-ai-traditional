# PATH: apps/products/migrations/0013_alter_products_sku.py

import uuid

from django.db import migrations, models


# FIX (Sep 2026): the straight AlterField below failed on your database
# with "value too long for type character varying(15)" — that means some
# products already have a SKU longer than 15 characters sitting in the
# table from before this limit existed. Postgres won't shrink the column
# while data that no longer fits is still in it, so this data migration
# runs first: any product whose current SKU is longer than 15 characters
# gets a fresh, short, still-unique SKU in the same "SKU-XXXXXXXX" format
# the model already auto-generates for blank SKUs (12 characters, well
# under the new 15-char limit). Products whose SKU already fits are left
# completely untouched.
def shorten_long_skus(apps, schema_editor):
    Product = apps.get_model('products', 'Product')

    existing_skus = set(
        Product.objects.values_list('sku', flat=True)
    )

    for product in Product.objects.filter(sku__isnull=False):
        if len(product.sku) <= 15:
            continue

        while True:
            new_sku = f"SKU-{uuid.uuid4().hex[:8].upper()}"
            if new_sku not in existing_skus:
                break

        existing_skus.add(new_sku)
        product.sku = new_sku
        product.save(update_fields=['sku'])


# No-op on the way back down — there's nothing meaningful to restore an
# auto-generated replacement SKU to, and the old long value is gone.
def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0012_product_reserved_stock_product_total_stock_and_more'),
    ]

    operations = [
        migrations.RunPython(shorten_long_skus, noop_reverse),
        migrations.AlterField(
            model_name='product',
            name='sku',
            field=models.CharField(blank=True, max_length=15, unique=True),
        ),
    ]