# PATH: apps/orders/migrations/0010_order_status_on_hold_choice.py

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0009_payment_qr_rejection_count'),
    ]

    operations = [
        migrations.AlterField(
            model_name='order',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending_payment', 'Pending Payment'),
                    ('on_hold', 'On Hold'),
                    ('confirmed', 'Confirmed'),
                    ('shipped', 'Shipped'),
                    ('out_for_delivery', 'Out for Delivery'),
                    ('delivered', 'Delivered'),
                    ('cancelled', 'Cancelled'),
                ],
                default='pending_payment',
                max_length=20,
            ),
        ),
    ]