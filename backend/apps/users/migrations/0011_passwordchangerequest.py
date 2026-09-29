import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0010_phonechangerequest'),
    ]

    operations = [
        migrations.CreateModel(
            name='PasswordChangeRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('new_password_hash', models.CharField(max_length=255)),
                ('otp_code', models.CharField(max_length=6)),
                ('otp_expires_at', models.DateTimeField()),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='password_change_request', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'password_change_requests',
            },
        ),
    ]