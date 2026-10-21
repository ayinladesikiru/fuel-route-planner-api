"""
Django settings for the Fuel Route Planner API.

Everything environment-specific is read from environment variables so the
same settings file works locally, in CI and in a container.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,0.0.0.0").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "rest_framework",
    "planner",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("SQLITE_PATH", BASE_DIR / "db.sqlite3"),
    }
}

# Route responses from the routing provider and geocoder are cached so that
# repeating a trip (or opening its map) never calls the external API again.
# Swap for Redis/Memcached in production via the standard Django CACHES setting.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fuel-route-planner",
        "OPTIONS": {"MAX_ENTRIES": 5000},
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
    "EXCEPTION_HANDLER": "planner.exceptions.api_exception_handler",
}

# --- Fuel route planner configuration -------------------------------------
FUEL_PLANNER = {
    # Free, key-less OSRM demo server. Point at a self-hosted OSRM in production.
    "OSRM_BASE_URL": os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org"),
    # Only used when a start/finish cannot be resolved by the offline gazetteer.
    "NOMINATIM_URL": os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search"),
    "HTTP_USER_AGENT": os.environ.get("HTTP_USER_AGENT", "fuel-route-planner/1.0 (technical assessment)"),
    "HTTP_TIMEOUT_SECONDS": float(os.environ.get("HTTP_TIMEOUT_SECONDS", "15")),
    "ROUTE_CACHE_SECONDS": 60 * 60 * 24,
    "GEOCODE_CACHE_SECONDS": 60 * 60 * 24 * 30,
    # Vehicle defaults (overridable per request).
    "MAX_RANGE_MILES": 500.0,
    "MILES_PER_GALLON": 10.0,
    # How far off the route (miles) a station may be and still be considered.
    "CORRIDOR_MILES": 10.0,
    # Keep only the cheapest station per N route-miles before optimising.
    # Keeps the plan to a sensible number of stops and the optimiser fast.
    "BUCKET_MILES": 25.0,
    # Dollar-equivalent cost of making one extra stop. Stops the optimiser from
    # pulling over to save a few cents; 0 = pure minimum fuel spend.
    "STOP_PENALTY_USD": 5.0,
    "GAZETTEER_PATH": BASE_DIR / "data" / "us_cities.csv",
    "FUEL_PRICES_PATH": BASE_DIR / "data" / "fuel-prices-for-be-assessment.csv",
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"planner": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")}},
}
