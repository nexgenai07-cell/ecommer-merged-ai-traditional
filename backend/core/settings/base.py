"""
Django base settings — shared across development and production.
"""

from pathlib import Path
from datetime import timedelta
import os
import dj_database_url
from dotenv import load_dotenv
import ssl
import certifi
# -------------------------------------------------
# BASE DIRECTORY + ENV
# -------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv()

# -------------------------------------------------
# SECURITY
# -------------------------------------------------

SECRET_KEY = os.getenv('SECRET_KEY', 'unsafe-default-key-change-me')

DEBUG = os.getenv('DEBUG', 'False') == 'True'

ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,.up.railway.app,*').split(',')

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Stripe configuration
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

# -------------------------------------------------
# EMAIL
# -------------------------------------------------

EMAIL_BACKEND = os.getenv(
    'EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend'
)
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True') == 'True'
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.getenv(
    'DEFAULT_FROM_EMAIL',
    'noreply@example.com'
)

FRONTEND_URL = os.getenv(
    'FRONTEND_URL',
    'http://localhost:5173'
)

EMAIL_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
EMAIL_TIMEOUT = 30
# -------------------------------------------------
# APPS
# -------------------------------------------------

INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',

    'apps.users',
    'apps.stores',
    'apps.categories',
    'apps.products',
    'apps.orders',
    'apps.cart',
    'apps.notifications',
    'apps.returns',
    'apps.analytics',
    'apps.ai',
    'apps.social',
    'apps.whatsapp',
    "apps.payments",
]

# -------------------------------------------------
# MIDDLEWARE
# -------------------------------------------------

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'

# -------------------------------------------------
# TEMPLATES
# -------------------------------------------------

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'
ASGI_APPLICATION = "core.asgi.application"

# -------------------------------------------------
# DATABASE
# -------------------------------------------------

DATABASE_URL = os.getenv('DATABASE_URL')

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            ssl_require=True,
        )
    }
else:
    # Fallback Neon Database Connection
    DATABASES = {
        "default": dj_database_url.parse(
            "postgresql://neondb_owner:npg_0HpNgaCXI1RE@ep-tiny-cloud-atmezn6j-pooler.c-9.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require",
            conn_max_age=600,
            ssl_require=True,
        )
    }

# -------------------------------------------------
# AUTH
# -------------------------------------------------

AUTH_USER_MODEL = 'users.User'

# -------------------------------------------------
# PASSWORD VALIDATION
# -------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'
    },
]

# -------------------------------------------------
# REST FRAMEWORK
# -------------------------------------------------

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticatedOrReadOnly',
    ),
    "DEFAULT_PAGINATION_CLASS": "core.pagination.StandardResultsPagination",
    "PAGE_SIZE": 10,
    "DEFAULT_THROTTLE_RATES": {
        "chat_user": "60/min",
        "chat_anon": "60/min",
    },
}

# -------------------------------------------------
# JWT
# -------------------------------------------------

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=60),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
}

# -------------------------------------------------
# CORS & CSRF
# -------------------------------------------------

# 1. CORS: Sab origins allow karne ke liye
CORS_ALLOW_ALL_ORIGINS = True

CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:3000",
]
if os.getenv('CORS_ALLOWED_ORIGINS'):
    CORS_ALLOWED_ORIGINS.extend([o.strip() for o in os.getenv('CORS_ALLOWED_ORIGINS').split(',') if o.strip()])

# 2. CSRF: Wildcard HTTP/HTTPS domains allow karne ke liye
CSRF_TRUSTED_ORIGINS = [
    "http://*",
    "https://*",
    "http://localhost:5173",
    "http://localhost:3000",
]

if os.getenv('CSRF_TRUSTED_ORIGINS'):
    CSRF_TRUSTED_ORIGINS.extend([o.strip() for o in os.getenv('CSRF_TRUSTED_ORIGINS').split(',') if o.strip()])
# -------------------------------------------------
# CHANNELS
# -------------------------------------------------

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}

# -------------------------------------------------
# INTERNATIONALIZATION
# -------------------------------------------------

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Karachi'
USE_I18N = True
USE_TZ = True

# -------------------------------------------------
# STATIC + MEDIA
# -------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_STORAGE = (
    "whitenoise.storage.CompressedManifestStaticFilesStorage"
)

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# -------------------------------------------------
# CACHE
# -------------------------------------------------

REDIS_URL = os.getenv("REDIS_URL", "")

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.dummy.DummyCache",
        }
    }

# ============================================
# QDRANT — Vector Database
# ============================================
QDRANT_URL = os.getenv('QDRANT_URL', 'http://localhost:6333')
QDRANT_API_KEY = os.getenv('QDRANT_API_KEY', None)
QDRANT_COLLECTION = 'products'

# ============================================
# GEMINI AI — multiple keys support
# ============================================
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

_gemini_keys_raw = os.getenv('GEMINI_API_KEYS', '')
if _gemini_keys_raw:
    GEMINI_API_KEYS = [k.strip() for k in _gemini_keys_raw.split(',') if k.strip()]
elif GEMINI_API_KEY:
    GEMINI_API_KEYS = [GEMINI_API_KEY]
else:
    GEMINI_API_KEYS = []

# ============================================
# GROQ & INTERNAL API
# ============================================
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
PORT = os.environ.get('PORT', '8000')
INTERNAL_API_URL = os.getenv('INTERNAL_API_URL', f'http://127.0.0.1:{PORT}')
