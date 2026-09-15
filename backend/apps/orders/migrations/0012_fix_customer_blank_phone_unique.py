# PATH: apps/orders/migrations/0012_fix_customer_blank_phone_unique.py
#
# FIX (Sep 2026 — IntegrityError creating a Customer for any user with no
# phone on their account): see the comment on Customer.Meta in models.py
# for the full explanation. Short version: unique_together on
# (phone, store) treated blank phone ("") as a real, colliding value —
# the second phoneless user in a store crashed with a duplicate-key
# error the moment their Customer row was first created. Replaced with
# an equivalent UniqueConstraint for (user, store), plus a
# UniqueConstraint for (phone, store) that only applies when phone isn't
# blank.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0011_checkoutotp'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='customer',
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name='customer',
            constraint=models.UniqueConstraint(
                fields=('user', 'store'),
                name='customers_user_store_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='customer',
            constraint=models.UniqueConstraint(
                condition=~models.Q(phone=''),
                fields=('phone', 'store'),
                name='customers_phone_store_nonblank_uniq',
            ),
        ),
    ]