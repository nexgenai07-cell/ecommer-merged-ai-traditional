# Generated manually — adds an optional profile picture field for
# reviewers, same CloudinaryField already used for ProductImage.image.

import cloudinary.models
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0020_review_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='review',
            name='profile_picture',
            field=cloudinary.models.CloudinaryField(
                blank=True,
                max_length=255,
                null=True,
                verbose_name='image',
            ),
        ),
    ]