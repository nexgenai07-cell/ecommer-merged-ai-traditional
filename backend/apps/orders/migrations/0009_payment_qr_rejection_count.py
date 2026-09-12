# PATH: apps/orders/migrations/0009_payment_qr_rejection_count.py

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0008_order_shipping_method_order_shipping_cost'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='qr_rejection_count',
            field=models.PositiveIntegerField(
                default=0,
                help_text="Number of times this order's QR proof has been rejected",
            ),
        ),
    ]