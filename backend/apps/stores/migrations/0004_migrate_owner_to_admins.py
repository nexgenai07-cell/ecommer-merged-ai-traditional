from django.db import migrations


def copy_owner_to_admins(apps, schema_editor):
    Store = apps.get_model('stores', 'Store')
    for store in Store.objects.all():
        if store.owner_id:
            store.admins.add(store.owner_id)


def reverse_copy(apps, schema_editor):
    # No-op: we don't need to reverse this, owner field still exists
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0003_store_admins_alter_store_owner'),  # yahan apni pichli migration ka naam daalo
    ]

    operations = [
        migrations.RunPython(copy_owner_to_admins, reverse_copy),
    ]