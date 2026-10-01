# Generated for Django 6.0.6 — product variants on order items (Oct 2026)

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0015_orderstatushistory'),
        ('products', '0023_productvariant_stockmovement_variant'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderitem',
            name='variant',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='order_items', to='products.productvariant'),
        ),
        migrations.AddField(
            model_name='orderitem',
            name='variant_label',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
    ]