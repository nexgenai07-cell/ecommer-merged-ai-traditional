from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0015_product_sku_reuse_after_soft_delete'),
    ]

    operations = [
        migrations.AlterField(
            model_name='product',
            name='sku',
            field=models.CharField(blank=True, max_length=25),
        ),
    ]