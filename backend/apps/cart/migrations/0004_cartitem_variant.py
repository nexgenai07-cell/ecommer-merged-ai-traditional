# Generated for Django 6.0.6 — product variants in the cart (Oct 2026)

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cart', '0003_wishlist_guest_support'),
        ('products', '0023_productvariant_stockmovement_variant'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='cartitem',
            unique_together=set(),
        ),
        migrations.AddField(
            model_name='cartitem',
            name='variant',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='cart_items', to='products.productvariant'),
        ),
        migrations.AddConstraint(
            model_name='cartitem',
            constraint=models.UniqueConstraint(condition=models.Q(('variant__isnull', True)), fields=('cart', 'product'), name='unique_cart_product_no_variant'),
        ),
        migrations.AddConstraint(
            model_name='cartitem',
            constraint=models.UniqueConstraint(condition=models.Q(('variant__isnull', False)), fields=('cart', 'product', 'variant'), name='unique_cart_product_variant'),
        ),
    ]