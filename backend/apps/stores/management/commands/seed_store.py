# PATH: apps/stores/management/commands/seed_store.py

import os
from django.core.management.base import BaseCommand
from django.conf import settings
from apps.users.models import User
from apps.stores.models import Store


class Command(BaseCommand):
    help = 'Creates one store and one superadmin if they do not already exist.'

    def handle(self, *args, **options):

        # ---------- 1. Create superadmin ----------
        admin_email = os.getenv('SUPERADMIN_EMAIL', 'admin@store.com')
        admin_password = os.getenv('SUPERADMIN_PASSWORD', 'Admin@12345')

        admin_user, created = User.objects.get_or_create(
            email=admin_email,
            defaults={
                'name': 'Super Admin',
                'role': 'admin',
                'is_staff': True,
                'is_superuser': True,
                'is_active': True,
            }
        )

        if created:
            admin_user.set_password(admin_password)
            admin_user.save()
            self.stdout.write(self.style.SUCCESS(f'Superadmin created: {admin_email}'))
        else:
            self.stdout.write(self.style.WARNING(f'Superadmin already exists: {admin_email}'))

        # ---------- 2. Create the single store ----------
        store, created = Store.objects.get_or_create(
            subdomain='main-store',
            defaults={
                'name': 'Main Store',
                'phone': '03000000000',
                'address': 'Pakistan',
                'plan': 'enterprise',
                'is_active': True,
            }
        )

        # UPDATED (v4.0): owner removed — admin_user ko admins M2M mein add karo
        # .add() idempotent hai, dobara run karne par duplicate nahi banega
        store.admins.add(admin_user)

        if created:
            self.stdout.write(self.style.SUCCESS(f'Store created: {store.name} (id={store.id})'))
        else:
            self.stdout.write(self.style.WARNING(f'Store already exists: {store.name} (id={store.id})'))

        self.stdout.write(self.style.SUCCESS('Seeding complete.'))
        self.stdout.write(self.style.SUCCESS(f'Store ID to remember: {store.id}'))