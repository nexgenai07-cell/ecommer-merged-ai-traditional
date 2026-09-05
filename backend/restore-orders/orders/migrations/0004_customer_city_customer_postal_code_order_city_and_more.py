# Generated for B18/B19/B22/F8/B15 checkout address fixes

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0003_alter_customer_user'),
    ]

    operations = [
        migrations.AddField(
            model_name='customer',
            name='city',
            field=models.CharField(max_length=100, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='customer',
            name='postal_code',
            field=models.CharField(max_length=20, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='order',
            name='city',
            field=models.CharField(max_length=100, blank=True, default=""),
        ),
        migrations.AddField(
            model_name='order',
            name='postal_code',
            field=models.CharField(max_length=20, blank=True, default=""),
        ),
        migrations.AddField(
            model_name='order',
            name='contact_phone',
            field=models.CharField(max_length=20, blank=True, default=""),
        ),
    ]