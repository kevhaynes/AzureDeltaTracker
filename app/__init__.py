import logging
import os

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Honour X-Forwarded-* when running behind a reverse proxy / App Gateway
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    if app.config["MOCK_MODE"]:
        from .mock_client import MOCK_TFE_CLIENT_IDS, MockAzureClient
        client = MockAzureClient(app.config)
        if not app.config["TFE_CLIENT_IDS"]:
            app.config["TFE_CLIENT_IDS"] = list(MOCK_TFE_CLIENT_IDS)
        if not (app.config["LOCAL_ADMIN_PASSWORD"] or app.config["LOCAL_ADMIN_PASSWORD_HASH"]):
            app.config["LOCAL_ADMIN_PASSWORD"] = "admin"
            app.logger.warning("MOCK_MODE: local admin password defaulted to 'admin'")
    else:
        missing = [k for k in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET") if not app.config[k]]
        if missing:
            raise RuntimeError(f"Missing required settings: {', '.join(missing)} (or set MOCK_MODE=true)")
        from .azure_client import AzureClient
        client = AzureClient(app.config)

    from .service import DeltaService
    app.extensions["delta_service"] = DeltaService(client, app.config)

    from . import auth, routes
    app.register_blueprint(auth.bp)
    app.register_blueprint(routes.bp)

    @app.context_processor
    def inject_globals():
        from flask import session
        return {"csrf_token": auth.csrf_token, "current_user": session.get("user"),
                "mock_mode": app.config["MOCK_MODE"]}

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:",
        )
        return resp

    return app
