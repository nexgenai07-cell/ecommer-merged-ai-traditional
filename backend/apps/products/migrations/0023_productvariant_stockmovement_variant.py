# Generated for Django 6.0.6 — product variants (Oct 2026)

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0022_alter_review_profile_picture'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProductVariant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('color', models.CharField(blank=True, default='', help_text='Color name, e.g. Black. Leave blank if this product has no color options.', max_length=50)),
                ('color_hex', models.CharField(blank=True, default='', help_text='Optional swatch color for the storefront, e.g. #000000.', max_length=7)),
                ('size', models.CharField(blank=True, default='', help_text='Size or kit, e.g. Large, 42, Body Only, 18-55mm Kit. Leave blank if this product has no size/kit options.', max_length=50)),
                ('price', models.DecimalField(decimal_places=2, max_digits=10)),
                ('original_price', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('total_stock', models.PositiveIntegerField(default=0, help_text='Total physical stock of this variant')),
                ('reserved_stock', models.PositiveIntegerField(default=0, help_text='Stock of this variant reserved for pending payment orders')),
                ('is_active', models.BooleanField(default=True)),
                ('is_delete', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='variants', to='products.product')),
            ],
            options={
                'db_table': 'product_variants',
                'ordering': ['id'],
            },
        ),
        migrations.AddConstraint(
            model_name='productvariant',
            constraint=models.UniqueConstraint(condition=models.Q(('is_delete', False)), fields=('product', 'color', 'size'), name='unique_active_product_variant'),
        ),
        migrations.AddField(
            model_name='stockmovement',
            name='variant',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='stock_movements', to='products.productvariant'),
        ),
    ]
    