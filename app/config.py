"""Application configuration, sourced from environment variables."""
import os
import secrets


def _bool(name, default=False):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _list(name, default=""):
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)

    # Data source
    MOCK_MODE = _bool("MOCK_MODE", False)
    MOCK_SEED = int(os.environ.get("MOCK_SEED", "42"))

    # App registration used to read Activity Logs / Resource Graph
    AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")
    AZURE_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "")
    AZURE_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "")
    AZURE_AUTHORITY_HOST = os.environ.get("AZURE_AUTHORITY_HOST", "https://login.microsoftonline.com")
    AZURE_MANAGEMENT_ENDPOINT = os.environ.get("AZURE_MANAGEMENT_ENDPOINT", "https://management.azure.com")

    # Use Resource Graph resourcechanges for before/after values (14 day retention)
    USE_RESOURCE_GRAPH_CHANGES = _bool("USE_RESOURCE_GRAPH_CHANGES", True)

    # Subscriptions excluded by default. Entries are subscription IDs or
    # case-insensitive glob patterns matched against subscription names.
    DEFAULT_EXCLUDED_SUBSCRIPTIONS = _list(
        "DEFAULT_EXCLUDED_SUBSCRIPTIONS", "*sandbox*,*sbx*,*preprod*,*pre-prod*"
    )

    # Client IDs of the service principals used by Terraform Enterprise instances
    TFE_CLIENT_IDS = [c.lower() for c in _list("TFE_CLIENT_IDS")]
    # Whether the "hide Terraform changes" toggle starts switched on
    TFE_FILTER_DEFAULT = _bool("TFE_FILTER_DEFAULT", True)

    CLICKOPS_TOP_N = int(os.environ.get("CLICKOPS_TOP_N", "10"))
    MAX_LOOKBACK_DAYS = int(os.environ.get("MAX_LOOKBACK_DAYS", "90"))
    CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "300"))
    FETCH_CONCURRENCY = int(os.environ.get("FETCH_CONCURRENCY", "8"))

    # Local admin login
    LOCAL_ADMIN_ENABLED = _bool("LOCAL_ADMIN_ENABLED", True)
    LOCAL_ADMIN_USERNAME = os.environ.get("LOCAL_ADMIN_USERNAME", "admin")
    # Prefer a werkzeug hash (see README); plain password is accepted for local use.
    LOCAL_ADMIN_PASSWORD_HASH = os.environ.get("LOCAL_ADMIN_PASSWORD_HASH", "")
    LOCAL_ADMIN_PASSWORD = os.environ.get("LOCAL_ADMIN_PASSWORD", "")

    # Entra ID (OIDC) sign-in
    ENTRA_AUTH_ENABLED = _bool("ENTRA_AUTH_ENABLED", False)
    ENTRA_TENANT_ID = os.environ.get("ENTRA_TENANT_ID") or AZURE_TENANT_ID
    ENTRA_CLIENT_ID = os.environ.get("ENTRA_CLIENT_ID") or AZURE_CLIENT_ID
    ENTRA_CLIENT_SECRET = os.environ.get("ENTRA_CLIENT_SECRET") or AZURE_CLIENT_SECRET
    ENTRA_REDIRECT_PATH = os.environ.get("ENTRA_REDIRECT_PATH", "/auth/callback")
    # Optional: full external redirect URI if behind a proxy
    ENTRA_REDIRECT_URI = os.environ.get("ENTRA_REDIRECT_URI", "")
    # Optional: restrict access to members of these group object IDs / app roles
    ENTRA_ALLOWED_GROUPS = _list("ENTRA_ALLOWED_GROUPS")
    ENTRA_ALLOWED_ROLES = _list("ENTRA_ALLOWED_ROLES")
