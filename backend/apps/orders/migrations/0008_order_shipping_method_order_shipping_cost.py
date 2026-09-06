from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0007_payment_payment_method_payment_qr_duplicate_warning_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="shipping_method",
            field=models.CharField(
                choices=[("standard", "Standard"), ("express", "Express")],
                default="standard",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="shipping_cost",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("299.00"),
                max_digits=10,
            ),
        ),
    ]