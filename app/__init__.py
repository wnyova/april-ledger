from flask import Flask

from config import Config
from .extensions import db


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)
    db.init_app(app)

    @app.errorhandler(413)
    def too_large(_error):
        return "File too large", 413

    from .routes import bp
    app.register_blueprint(bp)

    from .cli import register_cli
    register_cli(app)

    return app
