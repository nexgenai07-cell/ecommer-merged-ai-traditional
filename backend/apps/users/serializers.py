# PATH: apps/users/serializers.py
import logging
import re

from .models import User, UserSession, TwoFactorAuth
from rest_framework import serializers
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password

logger = logging.getLogger(__name__)

# Same rules as apps/orders/address_serializers.py, so an address given at
# registration is held to exactly the same standard as one added later
# from the Address Book.
_CITY_RE = re.compile(r'^[A-Za-z\s]+$')
_POSTAL_CODE_RE = re.compile(r'^\d{4,6}$')
_CITY_MAX_LENGTH = 30

# Other names the frontend register form might send the "Primary Address"
# field under. "address" is the main one; these are accepted as fallbacks.
_ADDRESS_KEY_ALIASES = ("primary_address", "shipping_address")


def _detect_city_from_address(address_text):
    """
    Registration only has ONE free-text "Primary Address" box, but an
    Address Book entry (and checkout) also needs a city. If no separate
    city was sent, look for a known Pakistani city inside the address text
    (e.g. "House 5, Street 3, Gujranwala" -> "Gujranwala"), scanning from
    the END of the text, since the city is normally written last.
    Returns "" when no known city is found (nothing is guessed).
    """
    from apps.orders.locations import provinces_for_city

    words = re.findall(r"[A-Za-z]+", address_text or "")
    for end in range(len(words), 0, -1):
        for size in (3, 2, 1):
            start = end - size
            if start < 0:
                continue
            candidate = " ".join(words[start:end])
            if provinces_for_city(candidate):
                return candidate.title()
    return ""


def _save_registration_address(user, address_text, city, postal_code):
    """
    Saves the address entered at registration as this customer's DEFAULT
    Address Book entry, so it shows up at checkout automatically (before
    the customer adds any new address).

    Registration must never fail because of this step, so any problem is
    logged and swallowed — the account itself is already created.
    """
    try:
        from apps.orders.models import Address
        from apps.orders.views import get_or_create_customer
        from apps.stores.models import Store

        store = Store.objects.first()
        if store is None:
            return

        customer = get_or_create_customer(user, store_id=store.id)

        Address.objects.create(
            customer=customer,
            label="Primary",
            shipping_address=address_text,
            city=city or _detect_city_from_address(address_text),
            postal_code=postal_code or None,
            phone=re.sub(r'[\s-]', '', user.phone or "") or None,
            is_default=True,
        )
    except Exception:
        logger.exception(
            "Could not save registration address for user %s", user.pk
        )


class RegisterSerializer(serializers.ModelSerializer):
    """
    Used for public registration.
    Role is always forced to 'customer' — admin cannot be created here.

    NEW (Sep 2026 — checkout prefill): the register form has a "Primary
    Address" box, but this serializer used to ignore it completely, so the
    address was never saved anywhere and could never show up at checkout.
    It is now accepted (optional) and stored as the customer's default
    Address Book entry. City / postal code are optional extras: if the
    frontend doesn't send a city, it is looked up from the address text.
    """

    address = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=500,
    )
    city = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=_CITY_MAX_LENGTH,
    )
    postal_code = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=20,
    )

    password = serializers.CharField(
        write_only=True,
        validators=[validate_password]
    )

    confirm_password = serializers.CharField(
        write_only=True
    )

    class Meta:
        model = User
        fields = [
            'name',
            'email',
            'phone',
            'address',
            'city',
            'postal_code',
            'password',
            'confirm_password'
        ]
        extra_kwargs = {
            'email': {
                'validators': []
            }
        }

    def to_internal_value(self, data):
        # Accept the address under a few common key names (see
        # _ADDRESS_KEY_ALIASES) and map it to "address".
        if hasattr(data, 'get') and not data.get('address'):
            for alias in _ADDRESS_KEY_ALIASES:
                if data.get(alias):
                    data = data.copy()
                    data['address'] = data.get(alias)
                    break
        return super().to_internal_value(data)

    def validate_address(self, value):
        value = (value or '').strip()
        if value and len(value) < 8:
            raise serializers.ValidationError(
                "Address looks too short — please enter a full address."
            )
        return value

    def validate_city(self, value):
        value = (value or '').strip()
        if value and not _CITY_RE.match(value):
            raise serializers.ValidationError(
                "City name should contain letters only (no numbers or symbols)."
            )
        return value

    def validate_postal_code(self, value):
        value = (value or '').strip()
        if value and not _POSTAL_CODE_RE.match(value):
            raise serializers.ValidationError(
                "Postal code should be 4-6 digits (leave blank if unknown)."
            )
        return value

    def validate_email(self, value):
      value = value.lower().strip()

      if User.objects.filter(email=value).exists():
        raise serializers.ValidationError(
            "A user with this email already exists."
        )

      return value

    def validate_phone(self, value):
        # FIX (Sep 2026 — international format registration bug): a local
        # Pakistani number like 03001234567 passed .isdigit() and
        # registered fine, but the SAME number as +923001234567 failed
        # with "must contain digits only" — .isdigit() returns False the
        # moment a '+' is in the string, so every +92 signup was silently
        # rejected. A leading '+' is now stripped before the digit/length
        # checks (a bare '+' with nothing after it still correctly fails),
        # so both 03XXXXXXXXX and +923XXXXXXXXX / 923XXXXXXXXX register.
        if not value:
            return value

        digits = value[1:] if value.startswith('+') else value

        if not digits or not digits.isdigit():
            raise serializers.ValidationError(
                "Phone number must contain digits only, optionally starting with '+'."
            )

        if len(digits) < 10:
            raise serializers.ValidationError(
                "Phone number must be at least 10 digits."
            )

        if len(digits) > 15:
            raise serializers.ValidationError(
                "Phone number must not exceed 15 digits."
            )

        return value

    def validate(self, data):

        if data['password'] != data['confirm_password']:
            raise serializers.ValidationError(
                {
                    'confirm_password': 'Passwords do not match.'
                }
            )

        return data

    def create(self, validated_data):

        validated_data.pop(
            'confirm_password'
        )

        # These three are not User fields — they belong to the Address
        # Book, so they are taken out before the User is created.
        address_text = validated_data.pop('address', '')
        city = validated_data.pop('city', '')
        postal_code = validated_data.pop('postal_code', '')

        user = User.objects.create_user(
            email=validated_data['email'],
            password=validated_data['password'],
            name=validated_data['name'],
            phone=validated_data.get(
                'phone',
                ''
            ),
            role='customer',
        )

        if address_text:
            _save_registration_address(user, address_text, city, postal_code)

        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    # Remember Me is an actual request field.
    # It must be declared at class level, NOT inside validate().
    remember_me = serializers.BooleanField(
        required=False,
        default=False
    )

    def validate(self, data):
        email = data.get("email")
        password = data.get("password")

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise serializers.ValidationError(
                "Invalid email or password."
            )

        # Check password manually because Django authenticate()
        # blocks is_active=False users before we can check is_delete.
        if not user.check_password(password):
            raise serializers.ValidationError(
                "Invalid email or password."
            )

        # Deleted account response
        # Keep this after password verification so nobody can
        # discover deleted accounts.
        if user.is_delete:
            raise serializers.ValidationError(
                {
                    "account_deactivated": True,
                    "email": user.email,
                    "message": (
                        "This account has been deleted. "
                        "Would you like to reactivate it?"
                    ),
                }
            )

        if not user.is_active:
            raise serializers.ValidationError(
                "This account has been deactivated."
            )

        data["user"] = user

        return data
class UserProfileSerializer(serializers.ModelSerializer):
    # Returns the logged-in user's profile information
    # and indicates whether two-factor authentication is enabled.
    """Used for GET /me/ and PUT /me/update/"""

    two_factor_enabled = serializers.SerializerMethodField()

    # NEW (Sep 2026 — Profile: personal info + picture, admin + customer):
    # addresses is read-only here — the full create/edit/delete/
    # set-default flow already exists at /api/v1/addresses/
    # (apps.orders.address_views); this just surfaces the saved list on
    # the same profile response so the frontend doesn't need a second
    # call to build the page. Admin users simply get an empty list here
    # (Address belongs to Customer, which is store-scoped and doesn't
    # exist for admins) rather than an error.
    addresses = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id',
            'name',
            'email',
            'phone',
            'profile_picture',
            'role',
            'email_verified',
            # NEW (Sep 2026 — checkout phone re-verification): lets the
            # frontend (e.g. the checkout page, by polling GET /me/) know
            # whether the CURRENT `phone` value above is verified — flips
            # to True together with email_verified at registration, and
            # again on its own after VerifyPhoneView confirms a changed
            # number.
            'phone_verified',
            'two_factor_enabled',
            'addresses',
            'created_at',
        ]
        read_only_fields = [
            'id',
            # email is intentionally read-only here — changing it now goes
            # through the dedicated OTP-verified flow instead
            # (POST /me/email/change/ + /me/email/confirm/, see
            # email_change_views.py), so a plain PUT/PATCH to this
            # endpoint can never silently move the account to an
            # unverified address.
            'email',
            'role',
            'email_verified',
            # phone_verified is only ever set by VerifyEmailView /
            # VerifyPhoneView, never by a direct profile PUT/PATCH.
            'phone_verified',
            'two_factor_enabled',
            'addresses',
            'created_at',
        ]

    def get_two_factor_enabled(self, obj):
        two_factor = getattr(obj, "two_factor", None)

        if two_factor:
            return two_factor.is_enabled

        return False

    def get_addresses(self, obj):
        # Local import to avoid a module-load-time circular import between
        # apps.users and apps.orders (orders.models only references
        # settings.AUTH_USER_MODEL by string, but apps.orders itself
        # imports apps.users.permissions in places, so this stays a
        # runtime-only import here rather than a top-of-file one).
        from apps.orders.models import Address
        from apps.orders.address_serializers import AddressSerializer

        addresses = Address.objects.filter(
            customer__user=obj
        ).order_by('-is_default', '-created_at')

        return AddressSerializer(addresses, many=True).data


class PasswordResetRequestSerializer(serializers.Serializer):
    # Validates password reset information and ensures
    # the new password confirmation matches.
    email = serializers.EmailField()

    def validate_email(self, value):
        if not User.objects.filter(email=value).exists():
            # Security note: in production, many APIs return success even if
            # email doesn't exist, to avoid leaking which emails are registered.
            # For a learning/client project, explicit error is fine and clearer.
            raise serializers.ValidationError('No account found with this email.')
        return value


class PasswordResetConfirmSerializer(serializers.Serializer):
    # Verifies the current password before allowing
    # the user to update it with a new secure password.
    token = serializers.CharField()
    uid = serializers.CharField()
    new_password = serializers.CharField(validators=[validate_password])
    confirm_password = serializers.CharField()

    def validate(self, data):
        if data['new_password'] != data['confirm_password']:
            raise serializers.ValidationError({'confirm_password': 'Passwords do not match.'})
        return data


class ChangePasswordSerializer(serializers.Serializer):
    """
    Used by ChangePasswordView.
    current_password is checked against the logged-in user's saved (hashed) password.
    """
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, validators=[validate_password])

    def validate_current_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            # check_password() safely compares the plain text input against
            # the hashed password stored in the database — this is the
            # standard, secure way to verify a password in Django.
            raise serializers.ValidationError('Current password is incorrect.')
        return value

    def validate(self, data):
        if data['current_password'] == data['new_password']:
            raise serializers.ValidationError({
                'new_password': 'New password must be different from the current password.'
            })
        return data


class DeleteAccountSerializer(serializers.Serializer):
    # Confirms the user's password before allowing
    # permanent account deletion.
    """Requires the user's password as confirmation before deleting — prevents
    accidental deletion or deletion by someone who briefly has device access."""
    password = serializers.CharField(write_only=True)

    def validate_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError('Incorrect password.')
        return value


class UserSessionSerializer(serializers.ModelSerializer):
    """Used by GET /sessions/ to list a user's active logins (devices/browsers)."""
    # Formats active login sessions and identifies
    # which session belongs to the current device.

    # FIX: "is_current" field ADD kiya gaya — pehle ye field exist hi nahi
    # karta tha, jab ke Requirements doc ke sample response mein
    # "is_current": true documented hai. Frontend isi field se batata hai
    # ke kaunsa device "this device" hai (jise sign-out button na dikhaye).
    # Current session ka pata request ke access token ke jti se chalta hai
    # (dekho views.py -> SessionListView / create_session_record).
    is_current = serializers.SerializerMethodField()

    class Meta:
        model = UserSession
        fields = ['id', 'device', 'browser', 'location', 'ip_address', 'is_current', 'last_active', 'created_at']

    def get_is_current(self, obj):
        request = self.context.get('request')
        if not request or not getattr(request, 'auth', None):
            return False
        try:
            current_access_jti = str(request.auth['jti'])
        except (KeyError, TypeError):
            return False
        return obj.access_jti == current_access_jti
    
class GoogleLoginSerializer(serializers.Serializer):
    id_token = serializers.CharField(required=True)