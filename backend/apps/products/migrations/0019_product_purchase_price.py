# Generated manually — adds admin-only cost price field for profit tracking

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0018_discount_code_reuser_after_soft_delete'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='purchase_price',
            field=models.DecimalField(
                blank=True,
                null=True,
                max_digits=10,
                decimal_places=2,
                help_text=(
                    "Admin-only cost price — what the store paid to "
                    "acquire this product. Used to calculate profit "
                    "(price - purchase_price). Never shown to customers."
                ),
            ),
        ),
    ]