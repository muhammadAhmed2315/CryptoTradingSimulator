import os
from models import User, Wallet, ValueHistory
from flask import jsonify
from extensions import db
from constants import GOOGLE_CLIENT_ID
from constants import GOOGLE_CLIENT_SECRET
from constants import TOKEN_GENERATOR_SALT
from constants import MOST_COMMON_PASSWORDS
from constants import GOOGLE_AUTHORIZATION_BASE_URL
from constants import GOOGLE_TOKEN_URL
from constants import GOOGLE_REDIRECT_URI
from constants import GOOGLE_USERINFO_URL
from constants import GOOGLE_SCOPE
from requests_oauthlib import OAuth2Session
from constants import DISCORD_OAUTH2_CLIENT_ID
from constants import DISCORD_API_BASE_URL
from constants import DISCORD_AUTHORIZATION_BASE_URL
from constants import DISCORD_TOKEN_URL
from login.forms import LoginForm, RegisterForm
from constants import TOKEN_GENERATOR_SECRET_KEY
from validate_email_address import validate_email
from constants import DISCORD_OAUTH2_CLIENT_SECRET
from constants import DISCORD_OAUTH2_REDIRECT_URI
from constants import PASSWORD_ALLOWED_SPECIAL_CHARS
from constants import TOKEN_GENERATOR_EXPIRATION_TIME_SECONDS
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from flask_login import (
    login_user,
    login_required,
    logout_user,
    current_user,
)
from flask import render_template, redirect, request, url_for, Blueprint, session
from flask_mail import Message
from login.forms import RequestPasswordResetForm, PasswordResetForm, PickUsernameForm

# This URLSafeTimedSerializer object will handle generating and verifying tokens
serializer = URLSafeTimedSerializer(TOKEN_GENERATOR_SECRET_KEY)
user_authentication = Blueprint("user_authentication", __name__)


# #################### DISCORD OAUTH AUTHENTICATION ####################
def token_updater(token):
    """
    Update the stored OAuth2 token in the session

    Args:
        token (dict): A dictionary containing the OAuth2 token

    Returns:
        None
    """
    session["oauth2_token"] = token


def make_session(token=None, state=None, scope=None):
    """
    Create an OAuth2 session for Discord authentication.

    Args:
        token (dict, optional): The OAuth token to be used for the session.
        state (str, optional): The state parameter from OAuth to prevent CSRF.
        scope (list, optional): The scopes required for the OAuth session.

    Returns:
        OAuth2Session: An instance of OAuth2Session configured for Discord.
    """

    return OAuth2Session(
        client_id=DISCORD_OAUTH2_CLIENT_ID,
        token=token,
        state=state,
        scope=scope,
        redirect_uri=DISCORD_OAUTH2_REDIRECT_URI,
        auto_refresh_kwargs={
            "client_id": DISCORD_OAUTH2_CLIENT_ID,
            "client_secret": DISCORD_OAUTH2_CLIENT_SECRET,
        },
        auto_refresh_url=DISCORD_TOKEN_URL,
        token_updater=token_updater,
    )


@user_authentication.route("/login_discord")
def login_discord():
    """
    Start the OAuth login process with Discord.

    This function redirects the user to the Discord authorization URL where they can
    authorize the application.

    Returns:
        Response: A redirection response to the Discord authorization URL.
    """
    scope = request.args.get("scope", "identify email")
    discord = make_session(scope=scope.split(" "))
    authorization_url, state = discord.authorization_url(DISCORD_AUTHORIZATION_BASE_URL)
    session["oauth2_state"] = state
    return redirect(authorization_url)


@user_authentication.route("/callback_discord")
def callback_discord():
    """
    Handle the OAuth callback from Discord.

    This function retrieves the OAuth token and user details, checks if the user exists
    in the database, and handles logging in or registering the user.

    Returns:
        Response: A redirection to another endpoint after handling the login or error.
    """
    if request.values.get("error"):
        return request.values["error"]
    discord = make_session(state=session.get("oauth2_state"))
    token = discord.fetch_token(
        DISCORD_TOKEN_URL,
        client_secret=DISCORD_OAUTH2_CLIENT_SECRET,
        authorization_response=request.url,
    )
    session["oauth2_token"] = token

    discord = make_session(token=session.get("oauth2_token"))
    user_info = jsonify(discord.get(DISCORD_API_BASE_URL + "/users/@me").json()).json
    user_email = user_info["email"]
    user_id = user_info["id"]

    # Check if user already exists in the database
    user = User.query.filter_by(email=user_email).first()
    if not user:
        # Create the user, add to the database and then login
        user = User(email=user_email, provider="discord", provider_id=user_id)
        db.session.add(user)
        db.session.commit()

    login_user(user)
    return redirect(url_for("user_authentication.pick_username"))


# #################### GOOGLE OAUTH AUTHENTICATION ####################
# os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # Only for development on localhost


@user_authentication.route("/login_google")
def login_google():
    """
    Initiates OAuth2 authentication with Google.

    This function creates an OAuth2 session with the specified client ID, redirect URI,
    and scope. It then constructs the authorization URL for Google's OAuth2 service,
    specifying that the access should be offline (allowing for refresh tokens) and
    prompting the user to select an account if multiple are logged in.

    The user is then redirected to the authorization URL to complete the login process.

    Returns:
        Redirect: A redirection response object that directs the user to Google's
                  OAuth2 login page.
    """
    google = OAuth2Session(
        GOOGLE_CLIENT_ID, redirect_uri=GOOGLE_REDIRECT_URI, scope=GOOGLE_SCOPE
    )
    authorization_url, state = google.authorization_url(
        GOOGLE_AUTHORIZATION_BASE_URL, access_type="offline", prompt=None
    )
    session["oauth_state"] = state
    return redirect(authorization_url)


@user_authentication.route("/callback_google")
def callback_google():
    """
    Handles the callback from Google OAuth2 authentication.

    This function retrieves the OAuth2 state from the session and uses it to create a new
    OAuth2Session. It then exchanges the authorization code returned by Google for an access token,
    which is then saved in the session.

    Subsequently, it fetches the user's email and ID from Google's userinfo endpoint.
    If the user does not already exist in the database, a new user record is created
    with details obtained from Google.

    Finally, it logs in the user and redirects them to the home page.

    Returns:
        Redirect: A redirection response object that directs the user to the home page
                  after login.
    """
    # Fetches and saves token in the session data
    google = OAuth2Session(
        GOOGLE_CLIENT_ID,
        state=session.get("oauth_state"),
        redirect_uri=GOOGLE_REDIRECT_URI,
    )
    token = google.fetch_token(
        GOOGLE_TOKEN_URL,
        client_secret=GOOGLE_CLIENT_SECRET,
        authorization_response=request.url,
    )
    session["google_token"] = token

    # Getting user email
    userinfo_response = google.get(GOOGLE_USERINFO_URL)
    userinfo = userinfo_response.json()
    user_email = userinfo.get("email")
    user_id = userinfo.get("id")

    # Check if user already exists in the database
    user = User.query.filter_by(email=user_email).first()
    if not user:
        # Create the user, add to the database and then login
        user = User(email=user_email, provider="google", provider_id=user_id)
        db.session.add(user)
        db.session.commit()

    login_user(user)

    return redirect(url_for("user_authentication.pick_username"))


# #################### DEFAULT AUTHENTICATION ####################
@user_authentication.route("/", methods=["get", "post"])
def login():
    """
    Endpoint to handle login requests. Redirects users who are already logged in to the
    home page. Handles form submission and validates login credentials. Successfully
    validated users are redirected to either the home page, or the restricted page
    they were trying to access.
    """
    # Redirect user if already logged in
    if current_user.is_authenticated and current_user.verified:
        return redirect(url_for("core.dashboard"))

    form = LoginForm()

    # Submit form handling
    if form.is_submitted() and form.validate():
        # Get user info from database
        user = User.query.filter_by(email=form.email.data).first()

        if not user:
            # If user doesn't exist
            form.password.errors.append("Incorrect email or password")
            return render_template("login/login.html", form=form)

        # If user exists and their password is correct
        if user.check_password(form.password.data):
            if user.verified == True:
                # If user is already verified
                login_user(user)

                next = request.args.get("next")

                if next == None or next[0] != "/":
                    next = url_for("core.dashboard")

                return redirect(next)
            else:
                # If user is not verified, resend verification link
                logout_user()
                token = generate_token(user.email, user.id)
                send_verification_email(user.email, token)
                return render_template(
                    "emailVerification/verification-link-sent.html",
                    user_email=user.email,
                )
        else:
            form.password.errors.append("Incorrect email or password")
            return render_template("login/login.html", form=form)

    return render_template("login/login.html", form=form)


@user_authentication.route("/register", methods=["get", "post"])
def register():
    """
    Endpoint to handle registration requests. Redirects users who are already logged in
    to the home page. Handles form submission, and validates the input by checking the
    email is in the correct format, checking against common passwords, and ensuring the
    password meets the complexity requirements. Register the user and add them to the
    database if successful, and then redirect them to the home page.
    """
    if current_user.is_authenticated:
        return redirect(url_for("core.dashboard"))

    form = RegisterForm()

    if form.is_submitted() and form.validate():
        input_email = form.email.data
        input_password = form.password.data
        input_pass_confirm = form.pass_confirm.data
        input_username = form.username.data

        # Confirm email is valid format
        if not validate_email(input_email):
            form.email.errors.append("Email is invalid")
            return render_template("login/register.html", form=form)

        # Confirm email is not already in use
        user = User.query.filter_by(email=input_email).first()
        if user:
            form.email.errors.append("Email is already registered")
            return render_template("login/register.html", form=form)

        # Confirm username is in valid format
        if not input_username[0].isalpha():
            form.username.errors.append("Username must begin with a letter")
            return render_template("/login/register.html", form=form)

        for char in input_username:
            if not char.isalpha() and not char.isdigit():
                form.username.errors.append("Username must be alphanumeric")
                return render_template("login/register.html", form=form)

        # Confirm username is not already in use
        user = User.query.filter_by(username=input_username).first()
        if user:
            form.username.errors.append("Username is already in use")
            return render_template("login/register.html", form=form)

        # Confirm password is not in too common list
        if input_password.lower() in MOST_COMMON_PASSWORDS:
            form.password.errors.append("This password is too common")
            form.password.errors.append(
                "Choose a less common password for better security"
            )
            return render_template("login/register.html", form=form)

        # Confirm password is correct format
        password_errors = check_password_format(input_password)

        if password_errors:
            for error in password_errors:
                form.password.errors.append(error)
            return render_template("login/register.html", form=form)

        # Confirm passwords match
        if input_password != input_pass_confirm:
            form.password.errors.append("Passwords do not match")
            form.pass_confirm.errors.append("Passwords do not match")
            return render_template("login/register.html", form=form)

        # Save user information to database, create a wallet for them, and log them in
        user = User(email=input_email, username=input_username, password=input_password)
        db.session.add(user)
        db.session.commit()

        wallet = Wallet(user.id)
        db.session.add(wallet)
        db.session.commit()

        valueHistory = ValueHistory(wallet.id)
        db.session.add(valueHistory)
        db.session.commit()

        login_user(user)

        # Redirect user to "You need to verify your email" page
        return redirect(url_for("user_authentication.verification_sent"))
    return render_template("login/register.html", form=form)


@user_authentication.route("/pick_username", methods=["get", "post"])
@login_required
def pick_username():
    """
    Allows a newly registered (OAuth) user to pick a username after signing up.

    This view function supports both GET and POST methods. It is intended for users who
    registered using an OAuth provider and don't yet have a username. This function
    enforces username requirements and checks for uniqueness in the database.

    Behavior:
    - If the user already has a username, they are redirected to the dashboard.
    - On GET request: Displays the username selection form.
    - On POST request: Validates the username's format and uniqueness, updates the
                       user's profile, and initializes related models like Wallet and
                       ValueHistory for the user.

    Returns:
    - If the user already has a username: Redirects to the dashboard.
    - If the form is submitted and valid: Updates the user's username and associated
      models, logs in the user, and redirects them to the dashboard.
    - If the form is submitted but invalid: Renders the form again with error messages.
    """
    # Redirect to the dashboard if user already has a username
    if current_user.username:
        return redirect(url_for("core.dashboard"))

    form = PickUsernameForm()

    if form.is_submitted() and form.validate():
        input_username = form.username.data

        # Check username is in valid format
        if not input_username[0].isalpha():
            form.username.errors.append("Username must begin with a letter")
            return render_template("login/pick-username.html", form=form)

        for char in input_username:
            if not char.isalpha() and not char.isdigit():
                form.username.errors.append("Username must be alphanumeric")
                return render_template("login/pick-username.html", form=form)

        # Confirm username is not already in use
        user = User.query.filter_by(username=input_username).first()
        if user:
            form.username.errors.append("Username is already in use")
            return render_template("login/pick-username.html", form=form)

        # Save username to database and redirect user to the home page
        current_user.update_username(input_username)
        db.session.add(current_user)
        db.session.commit()

        wallet = Wallet(current_user.id)
        db.session.add(wallet)
        db.session.commit()

        valueHistory = ValueHistory(wallet.id)
        db.session.add(valueHistory)
        db.session.commit()

        login_user(current_user)

        return redirect(url_for("core.dashboard"))

    return render_template("login/pick-username.html", form=form)


@user_authentication.route("/logout")
def logout():
    """
    View function for logging out the current user and redirecting them to the log in
    page.
    """
    logout_user()
    return redirect(url_for("user_authentication.login"))


# #################### VERIFY USER'S EMAIL ####################
@user_authentication.route("/verification_sent")
def verification_sent():
    """
    Endpoint to handle the process of sending a verification email to a user.

    Generates the verification token for the current user and sends a verification
    email to them. It then renders a template to display a confirmation message that
    the verification email has been sent.
    """
    if current_user.is_authenticated:
        if current_user.verified:
            return redirect(url_for("core.dashboard"))
    else:
        return redirect(url_for("user_authentication.login"))

    token = generate_token(current_user.email, current_user.id)
    send_verification_email(current_user.email, token, current_user.username)
    return render_template(
        "emailVerification/verification-link-sent.html", user_email=current_user.email
    )


@user_authentication.route("/verify_user/<token>")
def verify_user(token):
    """
    Endpoint to verify a user's email address using a provided token.

    This function attempts to decode and validate a token passed via URL. It checks the
    token's validity, age, and if the token's data matches a user's record. If the
    token is valid, and the user is not already verified, it marks the user as verified
    and updates the database. The user is redirected to different pages based on the
    outcome of the token validation.

    This endpoint is called when a user clicks a verification link in an email sent to
    them.

    Parameters:
    - token: The verification token sent to the user's email that needs to be validated
    """
    # Verify the token
    try:
        data = serializer.loads(
            token,
            salt=TOKEN_GENERATOR_SALT,
            max_age=TOKEN_GENERATOR_EXPIRATION_TIME_SECONDS,
        )

        user = User.query.filter_by(email=data["email"]).first()
        # Token is valid and matches data
        if user and str(user.id) == data["id"] and not user.verified:
            user.verified = True
            db.session.add(user)
            db.session.commit()
            return render_template(
                "emailVerification/verification-successful.html",
                user_email=data["email"],
            )
        else:
            return render_template("emailVerification/verification-token-invalid.html")

    except SignatureExpired:
        # Token has expired
        logout_user()
        data = serializer.loads_unsafe(token, salt=TOKEN_GENERATOR_SALT)[1]
        return render_template(
            "emailVerification/verification-token-expired.html",
            user_email=data["email"],
        )

    except BadSignature:
        # Token has been corrupted or tampered with or is invalid token
        logout_user()
        return render_template("emailVerification/verification-token-invalid.html")


def send_verification_email(user_email, token, username):
    """
    Sends a verification email to the specified user.

    Creates and sends an email which consists of a verification token embedded within
    an HTML template. Email is sent using the configured mail server to the user's
    email address.

    Parameters:
        user_email: Email address of user to whom the verification email will be sent
        token: The verification token to be included in the email for user verification
    """
    from app import mail_server

    html_body = render_template(
        "emailVerification/verification-email.html", token=token, username=username
    )
    msg = Message(
        subject="Verify Your CoinPulse Account",
        sender="MAIL_DEFAULT_SENDER",
        recipients=[user_email],
        html=html_body,
    )
    mail_server.send(msg)


# #################### RESET USER'S PASSWORD ####################
@user_authentication.route("/reset_password/<token>", methods=["get", "post"])
def reset_password(token):
    """
    Handles the password reset process using a provided security token.

    This view function supports both GET and POST methods and is used to validate a
    password reset token, show a password reset form if the token is valid, and update
    the user's password if the form is submitted and validated successfully.

    Parameters:
    token (str): The security token sent to the user's email for password reset
                 validation.

    Behavior:
    - Token verification: Checks if the token is valid, not expired, and not tampered
                          with or corrupted.
    - GET request: If the token is valid, displays the password reset form.
    - POST request: Validates the new password input and updates the password if all
                    conditions are met.

    Returns:
    - If the token is expired or invalid: Renders an appropriate error page.
    - If the form is submitted and valid: Updates the user's password and redirects to
      a success page.
    - If the form is submitted but invalid: Renders the form again with error messages.
    """
    # Verify the token
    try:
        data = serializer.loads(
            token,
            salt=TOKEN_GENERATOR_SALT,
            max_age=TOKEN_GENERATOR_EXPIRATION_TIME_SECONDS,
        )
    except SignatureExpired:
        # Token has expired
        data = serializer.loads_unsafe(token, salt=TOKEN_GENERATOR_SALT)[1]
        return render_template(
            "passwordReset/password-reset-token-expired.html", user_email=data["email"]
        )
    except BadSignature:
        # Token has been corrupted or tampered with or is invalid token
        return render_template("passwordReset/password-reset-token-invalid.html")

    # Token is valid
    user = User.query.filter_by(email=data["email"]).first()

    if user and str(user.id) == str(data["id"]):
        # Show password reset form
        form = PasswordResetForm()

        if form.is_submitted() and form.validate():
            input_password = form.password.data
            input_pass_confirm = form.pass_confirm.data

            # Confirm password is not in too common list
            if input_password.lower() in MOST_COMMON_PASSWORDS:
                form.password.errors.append("This password is too common")
                form.password.errors.append(
                    "Choose a less common password for better security"
                )
                return render_template(
                    "passwordReset/password-reset-form.html", form=form
                )

            # Confirm password is correct format
            password_errors = check_password_format(input_password)

            if password_errors:
                for error in password_errors:
                    form.password.errors.append(error)
                return render_template(
                    "passwordReset/password-reset-form.html", form=form
                )

            # Check passwords match
            if input_password != input_pass_confirm:
                form.pass_confirm.errors.append("Passwords do not match")
                return render_template(
                    "passwordReset/password-reset-form.html", form=form
                )

            # Update user password in the database
            user = db.session.get(User, data["id"])
            user.update_password(input_password)
            db.session.add(user)
            db.session.commit()

            return render_template("passwordReset/password-reset-successful.html")

        return render_template("passwordReset/password-reset-form.html", form=form)
    else:
        return render_template("passwordReset/password-reset-token-invalid.html")


@user_authentication.route("/forgot_password", methods=["get", "post"])
def forgot_password():
    """
    Handles the password reset request process for users who have forgotten their
    password.

    This view function supports both GET and POST methods. It displays a password reset
    request form and processes the form submission. If the form is submitted and
    validated successfully, it checks if the email provided exists in the database and
    is not linked to an OAuth provider. If these conditions are met, it generates a
    password reset token and sends a reset email to the user.

    Behavior:
    - If the user is already authenticated, they are redirected to the dashboard.
    - On GET request: Displays the password reset form.
    - On POST request: Validates the form and, if valid, sends a password reset email.
                       If the email does not exist in the database or is invalid, it
                       displays appropriate errors.

    Returns:
    - If the user is authenticated: Redirects to the dashboard.
    - If the form submission is invalid or an email is not found: Renders the form with
      error messages.
    - If the reset email is sent successfully: Renders a confirmation page indicating
      the email has been sent.
    """
    # Redirect user if already logged in
    if current_user.is_authenticated:
        return redirect(url_for("core.dashboard"))

    form = RequestPasswordResetForm()

    # Submit form handling
    if form.is_submitted() and form.validate():
        input_email = form.email.data

        # Confirm email is valid format
        if not validate_email(input_email):
            form.email.errors.append("Email is invalid")
            return render_template(
                "passwordReset/password-reset-request-email-form.html", form=form
            )

        # If user exists and isn't using OAuth for login
        user = User.query.filter_by(email=form.email.data).first()
        if user and not user.provider:
            token = generate_token(user_email=user.email, user_id=user.id)
            send_password_reset_email(
                user_email=user.email, token=token, username=user.username
            )

        # Redirect user to email sent page
        return render_template(
            "passwordReset/password-reset-email-successfully-sent.html"
        )

    return render_template(
        "passwordReset/password-reset-request-email-form.html", form=form
    )


def send_password_reset_email(user_email, token, username):
    """
    Sends a password reset email to a user.

    This function composes and sends an email with a password reset link that includes
    a security token. The email is sent to the user's email address provided during
    registration or stored in the user's profile.

    Parameters:
    user_email (str): The email address of the user to whom the password reset email
                      will be sent.
    token (str): A unique security token used for verifying the identity of the user
                 during the password reset process.
    username (str): The username of the user, used to personalize the email content.

    Returns:
    None: The function sends an email and does not return any value.
    """
    from app import mail_server

    html_body = render_template(
        "passwordReset/password-reset-email.html", token=token, username=username
    )
    msg = Message(
        subject="Reset your CoinPulse password",
        sender="MAIL_DEFAULT_SENDER",
        recipients=[user_email],
        html=html_body,
    )
    mail_server.send(msg)


# #################### HELPER FUNCTIONS ####################
def generate_token(user_email, user_id):
    """
    Generates a verification token for a user based on their email and user ID.

    This function encodes the user's email and ID into a token using a serializer with
    a specified salt. The resulting token can be used for verifying the user' email
    address.

    Parameters:
        user_email: Email address of the user for whom the token is being generated.
        user_id (int): The unique identifier of the user in the database (the primary
                       key)

    Returns:
        str: A serialized token that contains the user's email, ID, and the timestamp
             for when the token was generated, secured with a salt
    """
    data_to_encode = {"email": user_email, "id": str(user_id)}
    token = serializer.dumps(data_to_encode, salt=TOKEN_GENERATOR_SALT)
    return token


def check_password_format(input_password):
    """
    Validates the format of a user's password based on several criteria.

    This function checks if the provided password meets the following conditions:
    - Contains at least one uppercase letter.
    - Contains at least one lowercase letter.
    - Contains at least one digit (0-9).
    - Contains at least one special character from a predefined set
      (PASSWORD_ALLOWED_SPECIAL_CHARS).
    - Is at least 8 characters in length.

    Parameters:
    input_password (str): The password string to validate.

    Returns:
    list: A list of error messages for each criterion that the password fails to meet.
    An empty list indicates that the password meets all the criteria.
    """
    password_errors = []

    if not any(char.isupper() for char in input_password):
        password_errors.append("Password must include at least one uppercase letter")
    if not any(char.islower() for char in input_password):
        password_errors.append("Password must include at least one lowercase character")
    if not any(char.isdigit() for char in input_password):
        password_errors.append("Password must include at least one digit (0-9)")
    if not any(char in PASSWORD_ALLOWED_SPECIAL_CHARS for char in input_password):
        password_errors.append("Password must include at least one special character")
    if not len(input_password) >= 8:
        password_errors.append("Password must be at least 8 characters long")

    return password_errors
