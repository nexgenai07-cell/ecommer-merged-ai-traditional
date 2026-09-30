from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("stores", "0005_remove_store_owner"),
    ]

    operations = [
        migrations.AddField(
            model_name="store",
            name="payment_qr_image",
            field=models.ImageField(
                blank=True, null=True, upload_to="payment_qr/"
            ),
        ),
    ]
