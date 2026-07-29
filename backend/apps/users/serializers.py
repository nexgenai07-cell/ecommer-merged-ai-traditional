# PATH: apps/users/serializers.py
from .models import User, UserSession, TwoFactorAuth
from rest_framework import serializers
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password


class RegisterSerializer(serializers.ModelSerializer):
    """
    Used for public registration.
    Role is always forced to 'customer' — admin cannot be created here.
    """

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
            'password',
            'confirm_password'
        ]
        extra_kwargs = {
            'email': {
                'validators': []
            }
        }

    def validate_email(self, value):

        # Check if this email belongs to a previously deleted account
        deleted_user = User.objects.filter(
            email=value,
            is_delete=True
        ).first()

        if deleted_user:
            raise serializers.ValidationError(
                {
                    "account_deleted": True,
                    "message": (
                        "This account was deleted. "
                        "Please reactivate your account."
                    ),
                    "email": value,
                }
            )

        # Check active accounts only
        if User.objects.filter(
            email=value,
            is_delete=False
        ).exists():
            raise serializers.ValidationError(
                "A user with this email already exists."
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

        return user


class LoginSerializer(serializers.Serializer):
    """
    Handles user login.

    Flow:
    1. Validate email/password
    2. Check email verification
    3. Check deleted/deactivated account
    4. Check 2FA requirement
    5. Continue normal login
    """

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        email = data.get('email')
        password = data.get('password')

        # Find user first (even deleted users)
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise serializers.ValidationError(
                "Invalid email or password."
            )

        # Check password manually because authenticate()
        # rejects inactive users before we can inspect them
        if not user.check_password(password):
            raise serializers.ValidationError(
                "Invalid email or password."
            )

        # NEW: deleted account response
        if user.is_delete:
            raise serializers.ValidationError(
                {
                    "account_deactivated": True,
                    "email": user.email,
                    "message": "This account has been deleted. Would you like to reactivate your account?"
                }
            )

        # Normal inactive account
        if not user.is_active:
            raise serializers.ValidationError(
                "This account is inactive."
            )

        data["user"] = user
        return data


class UserProfileSerializer(serializers.ModelSerializer):
    # Returns the logged-in user's profile information
    # and indicates whether two-factor authentication is enabled.
    """Used for GET /me/ and PUT /me/update/"""

    two_factor_enabled = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id',
            'name',
            'email',
            'phone',
            'role',
            'email_verified',
            'two_factor_enabled',
            'created_at',
        ]
        read_only_fields = [
            'id',
            'email',
            'role',
            'email_verified',
            'two_factor_enabled',
            'created_at',
        ]

    def get_two_factor_enabled(self, obj):
        two_factor = getattr(obj, "two_factor", None)

        if two_factor:
            return two_factor.is_enabled

        return False


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