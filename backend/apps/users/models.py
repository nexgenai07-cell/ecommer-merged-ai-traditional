# PATH: apps/users/models.py

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.conf import settings
import secrets
from datetime import timedelta
from django.utils import timezone

# Custom manager responsible for creating normal users
# and superusers using email instead of username.
class UserManager(BaseUserManager):

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email)
        extra_fields.setdefault('role', 'customer')
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user
    # Creates a normal user with a hashed password.
    # If no role is supplied, the user becomes a customer by default.
    # Creates an admin/superuser with all required permissions.
    # Custom user model that uses email as the unique login field
    # and stores additional information like role and phone number.

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('role', 'admin')
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):

    ROLE_CHOICES = [
        ('admin',      'Admin'),
        ('moderator',  'Moderator'),  # will be used later
        ('customer',   'Customer'),
    ]

    name            = models.CharField(max_length=255)
    email           = models.EmailField(unique=True)
    phone           = models.CharField(max_length=20, blank=True, null=True)
    role            = models.CharField(max_length=20, choices=ROLE_CHOICES, default='customer')

    # NEW (Sep 2026 — Profile picture, admin + customer): stored via
    # Cloudinary automatically, same as every other image upload in this
    # project (see settings.DEFAULT_FILE_STORAGE) — no separate storage
    # setup needed. Works for both roles since it's on the base User
    # model, not Customer (which is store-scoped and doesn't exist for
    # admins at all).
    profile_picture = models.ImageField(
        upload_to='profile_pictures/',
        null=True,
        blank=True,
    )

    # True once the user clicks the link sent by send-verification-email/.
    # Kept separate from is_active so an unverified account can still log in
    # (we just show a "please verify" banner) rather than being locked out.
    email_verified  = models.BooleanField(default=False)

    # NEW (Sep 2026 — combined email+phone verification): flips to True at
    # the same moment email_verified does, when the user clicks the
    # registration verification link (see VerifyEmailView) — one link now
    # verifies both the email AND whatever phone number was on the account
    # at signup. If the customer later changes their phone at checkout to a
    # number different from this verified one, this flips back to False for
    # that new number until they verify it too (see PhoneVerification /
    # SendPhoneVerificationView / VerifyPhoneView below).
    phone_verified  = models.BooleanField(default=False)

    is_active       = models.BooleanField(default=True)
    is_staff        = models.BooleanField(default=False)
    # Soft-delete flag. Matches migration 0004_user_is_delete, which added
    # this column to the database, but the field was missing from this
    # model class — causing AttributeError: 'User' object has no attribute
    # 'is_delete' whenever serializers.py / views.py accessed user.is_delete.
    is_delete       = models.BooleanField(default=False)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD  = 'email'
    REQUIRED_FIELDS = ['name']

    class Meta:
        db_table = 'users'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.email} ({self.role})'

    @property
    def is_admin(self):
        return self.role == 'admin'

    @property
    def is_customer(self):
        return self.role == 'customer'


class UserSession(models.Model):
    """
    Tracks one row per login (per device/browser). This is what makes the
    "Active Sessions" feature on the frontend possible — without this table,
    JWT alone has no concept of "sessions" at all (it's stateless by design).

    A row is created every time a user logs in successfully (see LoginView).
    It stores the refresh token's unique ID (jti) so we can blacklist that
    SPECIFIC session later without logging out every other device.
    """
    user           = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sessions')
    refresh_jti    = models.CharField(max_length=255, unique=True, help_text='JWT ID of the refresh token for this session')
    # FIX: added so GET /sessions/ can tell the frontend WHICH row is the
    # session currently being used to make the request (doc requires an
    # "is_current" field, which was missing entirely before). We store the
    # ACCESS token's jti (not the refresh token's) because the request that
    # calls /sessions/ only carries an access token, and we compare against
    # this at read time in UserSessionSerializer.
    access_jti     = models.CharField(max_length=255, blank=True, default='', help_text='JWT ID of the access token issued at the same login, used to detect the current session')
    device         = models.CharField(max_length=255, blank=True, default='Unknown device')
    browser        = models.CharField(max_length=100, blank=True, default='Unknown browser')
    ip_address     = models.GenericIPAddressField(null=True, blank=True)
    location       = models.CharField(max_length=255, blank=True, default='Unknown')
    last_active    = models.DateTimeField(auto_now=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'user_sessions'
        ordering = ['-last_active']

    def __str__(self):
        return f'{self.user.email} — {self.device} ({self.browser})'
    # Stores information about every login session so users can
    # view and manage active devices independently.


class TwoFactorAuth(models.Model):
    """
    One row per user. Stores an emailed 6-digit OTP used for two-factor
    authentication instead of an authenticator-app TOTP secret.

    is_enabled becomes True only AFTER the user receives the OTP by email
    and successfully verifies it — this proves the email address is
    actually reachable before we start requiring 2FA on every login.
    """
    user            = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='two_factor')
    secret          = models.CharField(max_length=64, blank=True, null=True)  # legacy, no longer used
    otp_code        = models.CharField(max_length=6, blank=True, null=True)
    otp_expires_at  = models.DateTimeField(blank=True, null=True)
    is_enabled      = models.BooleanField(default=False)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'two_factor_auth'

    def __str__(self):
        return f'2FA for {self.user.email} ({"enabled" if self.is_enabled else "pending"})'
    
class EmailVerification(models.Model):
    # Creates and validates email verification tokens used during account verification.
    """
    One row per verification email sent. The token is single-use (is_used
    flips to True once consumed) and expires after 24 hours by default,
    matching the same pattern used for password reset links.
    """
    user        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='email_verifications')
    token       = models.CharField(max_length=64, unique=True)
    is_used     = models.BooleanField(default=False)
    expires_at  = models.DateTimeField()
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'email_verifications'

    def is_valid(self):
        return not self.is_used and timezone.now() < self.expires_at

    @classmethod
    def create_for_user(cls, user, validity_hours=24):
        return cls.objects.create(
            user=user,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timedelta(hours=validity_hours),
        )


class PhoneVerification(models.Model):
    """
    NEW (Sep 2026 — checkout phone re-verification): one row per
    phone-verification LINK sent (mirrors EmailVerification exactly —
    token, single-use, 24h expiry). Used only when a customer wants to use
    a phone number DIFFERENT from the one already verified on their
    account (e.g. edited at checkout) — the original registration phone is
    verified together with the email itself (see VerifyEmailView), so this
    model is never involved at signup time.

    No SMS gateway is wired up yet, so — same as every other OTP/link flow
    in this project — the link is emailed to the account's email address,
    not texted to the phone. Clicking it sets User.phone to this row's
    `phone` and User.phone_verified to True.
    """
    user        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='phone_verifications')
    phone       = models.CharField(max_length=20)
    token       = models.CharField(max_length=64, unique=True)
    is_used     = models.BooleanField(default=False)
    expires_at  = models.DateTimeField()
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'phone_verifications'

    def is_valid(self):
        return not self.is_used and timezone.now() < self.expires_at

    @classmethod
    def create_for_user(cls, user, phone, validity_hours=24):
        return cls.objects.create(
            user=user,
            phone=phone,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timedelta(hours=validity_hours),
        )


# NEW (Sep 2026 — Profile: editable email with OTP verification): a
# customer/admin can now change their own email from the profile page,
# but changing it directly (like name/phone) would let a typo or a
# hijacked session silently lock the real owner out, or let someone move
# the account to an email they don't actually control. This mirrors the
# TwoFactorAuth OTP pattern (6-digit code, 10-minute expiry) rather than
# EmailVerification's token-link pattern, since the user is sitting on
# the profile page already and can type a code straight back in.
#
# One row per user (OneToOneField) — requesting a new change overwrites
# any still-pending one, so only the most recent code is ever valid.
class EmailChangeRequest(models.Model):
    user           = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='email_change_request',
    )
    new_email      = models.EmailField()
    otp_code       = models.CharField(max_length=6)
    otp_expires_at = models.DateTimeField()
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'email_change_requests'

    def __str__(self):
        return f'{self.user.email} -> {self.new_email}'

    def is_valid(self):
        return timezone.now() < self.otp_expires_at