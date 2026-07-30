"""
Django base settings — shared across development and production.
"""

from pathlib import Path
from datetime import timedelta
import os
import warnings
import dj_database_url
from dotenv import load_dotenv

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
# REDIS
# -------------------------------------------------
# NEW — FIX: moved up from the bottom of the file (was only declared right
# before CACHES) so the SAME value can also be used by CHANNEL_LAYERS below.
# Both the cache (rate limiting + admin pending-actions store) and the
# channel layer (WebSocket group broadcasts, e.g. the admin confirm/cancel
# push) need Redis to work correctly in production — see the comments in
# both blocks below for what silently breaks without it.

REDIS_URL = os.getenv("REDIS_URL", "")

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

CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:3000",
]
if os.getenv('CORS_ALLOWED_ORIGINS'):
    CORS_ALLOWED_ORIGINS.extend([o.strip() for o in os.getenv('CORS_ALLOWED_ORIGINS').split(',') if o.strip()])

CSRF_TRUSTED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:3000",
]
if os.getenv('CSRF_TRUSTED_ORIGINS'):
    CSRF_TRUSTED_ORIGINS.extend([o.strip() for o in os.getenv('CSRF_TRUSTED_ORIGINS').split(',') if o.strip()])

# -------------------------------------------------
# CHANNELS
# -------------------------------------------------
# FIX: was hardcoded to InMemoryChannelLayer, which only delivers
# channel_layer.group_send() messages to consumers running in the SAME
# process. apps/ai/admin_action_views.py (ConfirmAdminActionView /
# CancelAdminActionView — plain synchronous DRF requests, NOT WebSocket
# consumers) now pushes the admin confirm/cancel result to the open
# "admin_chat_<session_key>" WebSocket group via group_send(). If Railway
# ever runs more than one worker/process — or the HTTP request and the
# WebSocket connection simply land on different processes — those pushed
# messages would silently vanish (no error, the admin's chat would just
# never update). Redis-backed channel layer fixes this by sharing group
# membership/messages across processes, using the same REDIS_URL already
# configured for CACHES below.
#
# Requires the `channels_redis` package (pip install channels_redis /
# add to requirements.txt) — falls back to InMemoryChannelLayer only when
# REDIS_URL isn't set (e.g. local dev without Redis running).
#
# UPDATE — FIX for "TimeoutError: Timeout reading from ...upstash.io:6379":
# Upstash (a serverless Redis) silently drops idle TCP connections after a
# short period. channels_redis keeps a small pool of long-lived connections
# open for pub/sub, so once Upstash kills one from its side, the next read
# on that dead socket just hangs until Python's own timeout fires — which
# is exactly the crash-on-every-connection behaviour reported. Passing
# these extra per-host options makes the underlying redis-py client detect
# dead sockets and reconnect instead of hanging:
#   - socket_keepalive: keeps the OS-level TCP connection alive so idle
#     sockets aren't silently dropped without either side noticing.
#   - socket_connect_timeout / socket_timeout: bound how long a single
#     operation can hang before redis-py raises promptly instead of the
#     connection hanging indefinitely.
#   - retry_on_timeout: on a timeout, redis-py retries the operation on a
#     fresh connection instead of just propagating the crash upward.
#   - health_check_interval: periodically pings idle connections in the
#     pool so a dead one is caught and replaced before a real message needs
#     to go through it.

if REDIS_URL:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [{
                    "address": REDIS_URL,
                    "socket_keepalive": True,
                    "socket_connect_timeout": 5,
                    "socket_timeout": 5,
                    "retry_on_timeout": True,
                    "health_check_interval": 30,
                }],
            },
        },
    }
else:
    warnings.warn(
        "REDIS_URL not set — falling back to InMemoryChannelLayer. "
        "WebSocket group broadcasts (e.g. admin confirm/cancel push) will "
        "only work within a single process. Set REDIS_URL in production.",
        RuntimeWarning,
    )
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
# FIX: previously fell back to DummyCache when REDIS_URL wasn't set.
# DummyCache's get() ALWAYS returns None and set() is a silent no-op — no
# exception is ever raised. That silently disables two security-relevant
# features with zero warning:
#   1. apps/ai/rate_limiting.py — check_rate_limit() reads
#      `cache.get(cache_key) or []`, which is always `[]` with DummyCache,
#      so the sliding-window history never accumulates and the limit can
#      NEVER trip, no matter how large a burst is sent. This matches
#      exactly what was reported: 11 rapid messages, zero rejections.
#   2. apps/ai/admin_tools/pending_actions.py — the admin confirm/cancel
#      preview (action_id) is stored via this same cache; with DummyCache
#      it would never actually persist between requests.
# The rate_limiting.py fail-closed `except Exception` guard doesn't help
# here either, since DummyCache doesn't raise — it just quietly does
# nothing. LocMemCache actually stores data (in-process), so both features
# work correctly for a single-process deployment; Redis is still required
# for correctness across multiple worker processes/replicas — set
# REDIS_URL on Railway for production.

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    warnings.warn(
        "REDIS_URL not set — falling back to LocMemCache. WS rate "
        "limiting and admin action confirmations will only work correctly "
        "within a single process (data is not shared across workers/"
        "replicas). Set REDIS_URL in production.",
        RuntimeWarning,
    )
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
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
INTERNAL_API_URL = os.getenv('INTERNAL_API_URL', 'http://localhost:8000')