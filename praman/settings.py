"""
Django settings for Praman.

Reads secrets and runtime config from .env via python-decouple.
"""

from pathlib import Path

from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Security ---

SECRET_KEY = config(
    "DJANGO_SECRET_KEY",
    default="django-insecure-dev-only-replace-via-env-for-anything-real",
)

DEBUG = config("DJANGO_DEBUG", default=True, cast=bool)

ALLOWED_HOSTS = config(
    "DJANGO_ALLOWED_HOSTS",
    default="127.0.0.1,localhost",
    cast=Csv(),
)

# --- Apps ---

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "django_htmx",
    # Local
    "core",
    "officer",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "praman.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "praman.wsgi.application"

# --- Database ---

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# --- Auth ---

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- I18n / time ---

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# --- Auth flow (Day 7) ---
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/officer/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

# --- Static + media ---

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- File upload limits (tender PDFs can be large) ---

DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024  # 50 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024

# --- Praman-specific runtime config ---

GEMINI_API_KEY = config("GEMINI_API_KEY", default="")
# Vision-only key isolates rate-limit pressure from the extraction layer.
# Falls back to the general key if a dedicated one is not set.
GEMINI_VISION_API_KEY = config("GEMINI_VISION_API_KEY", default="") or GEMINI_API_KEY
GEMINI_MODEL_PRO = config("GEMINI_MODEL_PRO", default="gemini-2.5-pro")
GEMINI_MODEL_FLASH = config("GEMINI_MODEL_FLASH", default="gemini-2.5-flash")

OPA_URL = config("OPA_URL", default="http://127.0.0.1:8181")

OCR_CONFIDENCE_THRESHOLD = config(
    "OCR_CONFIDENCE_THRESHOLD", default=0.85, cast=float
)

# Synthetic data path (gitignored)
SYNTHETIC_DIR = BASE_DIR / "synthetic"
