import os
import logging
from flask import Flask
from models import User
from flask_mail import Mail
from flask_migrate import Migrate
from constants import MAIL_USERNAME
from constants import MAIL_PASSWORD
from extensions import db, login_manager

# Configure logging
log = logging.getLogger("werkzeug")


def create_app():
    """
    Creates an configures an instance of a Flask application.

    Sets up configuration settings for the app and the associated database. Sets up the
    database and the login manager. Registers blueprints and prepares database tables
    if necessary.

    Returns:
        app: Configured Flask application
    """
    # Initialize Flask app
    app = Flask(__name__)

    # Configure app
    app.config["SECRET_KEY"] = "mysecretkey"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    if os.environ.get("DATABASE_URL"):
        app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL").replace(
            "postgres://", "postgresql://"
        )
    else:
        basedir = os.path.abspath(os.path.dirname(__file__))
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(
            basedir, "data.sqlite"
        )

    # Configure mail
    app.config["MAIL_SERVER"] = "smtp.gmail.com"
    app.config["MAIL_PORT"] = 465
    app.config["MAIL_USERNAME"] = MAIL_USERNAME
    app.config["MAIL_PASSWORD"] = MAIL_PASSWORD
    app.config["MAIL_USE_TLS"] = False
    app.config["MAIL_USE_SSL"] = True

    # Initialise Mail object
    mail_server = Mail(app)

    # Initialize database
    db.init_app(app)
    Migrate(app, db)

    # Initialize login manager
    login_manager.init_app(app)
    login_manager.login_view = "user_authentication.login"

    @login_manager.user_loader
    def load_user(user_id):
        """
        Required user_loader callback for Flask-Login, loads a user from the database.

        Parameters:
            user_id: The user ID stored in the session

        Returns:
            User: The user object if found, None otherwise
        """
        return db.session.get(User, int(user_id))

    # Import blueprints
    from login.app import user_authentication
    from home.app import home

    # Register blueprints
    app.register_blueprint(user_authentication)
    app.register_blueprint(home)

    # Create database tables
    with app.app_context():
        db.create_all()

    return app, mail_server


# Create the app instance
app, mail_server = create_app()

if __name__ == "__main__":
    app.run()
