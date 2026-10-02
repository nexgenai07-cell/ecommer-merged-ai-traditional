# Generated for Django 6.0.6 — per-color product images (Oct 2026)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0023_productvariant_stockmovement_variant'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='productimage',
            options={'ordering': ['color', 'id']},
        ),
        migrations.AddField(
            model_name='productimage',
            name='color',
            field=models.CharField(blank=True, default='', max_length=50),
        ),
    ]