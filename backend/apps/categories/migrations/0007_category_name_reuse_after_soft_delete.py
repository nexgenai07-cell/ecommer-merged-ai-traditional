# PATH: apps/categories/migrations/0007_category_name_reuse_after_soft_delete.py

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('categories', '0006_category_is_delete'),
    ]

    operations = [
        # Drop the old, unconditional unique=True on name — it blocked
        # reusing a soft-deleted category's name forever.
        migrations.AlterField(
            model_name='category',
            name='name',
            field=models.CharField(max_length=255),
        ),
        # Replace it with a partial unique index that only applies to
        # rows where is_delete=False, so a soft-deleted category's name
        # genuinely frees up for a new category to use.
        migrations.AddConstraint(
            model_name='category',
            constraint=models.UniqueConstraint(
                fields=['name'],
                condition=models.Q(is_delete=False),
                name='unique_active_category_name',
            ),
        ),
    ]