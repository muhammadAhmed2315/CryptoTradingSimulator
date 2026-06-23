import time
import uuid
from decimal import Decimal

from flask_login import UserMixin
from sqlalchemy import ARRAY, Boolean
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.mutable import MutableDict, MutableList
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db
from money import D, DUST_QTY, qty_get, qty_set, quantize_qty, quantize_usd


class User(db.Model, UserMixin):
    """
    User model class (for the database) that supports authentication and additional
    properties including support for OAuth providers.

    Attributes:
        id: Unique identifier for the user, serves as the primary key
        email: User's email address, must be unique
        password_hash: Hashed and salted version of the user's password for secure
                       storage
        provider: Name of the OAuth provider, if relevant for that account
        provider_id: Identifier from the OAuth provider, if relevant for that account
        verified: Boolean flag to indicate whether the user's email is verified
    """

    __tablename__ = "users"
    __table_args__ = (
        # Case-insensitive uniqueness -- matches the uq_users_email_lower
        # functional UNIQUE index from migration 0007. The app lowercases every
        # email, so this is the authoritative guarantee (replaces UNIQUE (email)).
        # NB: use text("lower(email)") so the expression targets the email COLUMN.
        # func.lower("email") would index the constant string 'email' (one row max).
        db.Index("uq_users_email_lower", db.text("lower(email)"), unique=True),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = db.Column(db.String(254), nullable=False)
    username = db.Column(db.String(20), unique=True)
    password_hash = db.Column(db.Text, nullable=True)
    provider = db.Column(db.Text, nullable=True)
    provider_id = db.Column(db.Text, nullable=True)
    verified = db.Column(Boolean, default=False, nullable=False)
    last_password_reset_token = db.Column(db.Text, nullable=True)
    wallet = db.relationship("Wallet", backref="owner", uselist=False)

    def __init__(
        self, email, username=None, password=None, provider=None, provider_id=None
    ):
        """
        Initialises a new User instance.

        Parameters:
            email: User's email address
            password: User's password; will be hashed and salted before being stored in
                      the database
            provider: The OAuth provider's name (if applicable)
            provider_id: The OAuth provider's user identifier (if applicable)
        """
        self.email = email
        if username:
            self.username = username
        if password:
            self.password_hash = generate_password_hash(password)
        if provider and provider_id:
            self.provider = provider
            self.provider_id = provider_id

    def update_username(self, username: str) -> None:
        """
        Updates the username for the user.

        Parameters:
            username (str): The new username to be assigned to the user.
        """
        self.username = username

    def update_password(self, password: str) -> None:
        """
        Updates the password for the user. If the user is not authenticated via an
        an external provider (e.g., OAuth), this function will update the user's
        hashed password in the database.

        Parameters:
            password (str): The new password to be hashed and stored.
        """
        if not self.provider and not self.provider_id:
            self.password_hash = generate_password_hash(password)

    def update_last_password_reset_token(self, token: str) -> None:
        self.last_password_reset_token = token

    def check_password(self, password: str):
        """
        Checks input password against the stored hash

        Parameters:
            password: The password to verify

        Returns:
            bool: True if password matches hash, else false
        """
        if self.password_hash:
            return check_password_hash(self.password_hash, password)
        return False


class Wallet(db.Model):
    """
    Wallet model class (for the database) that stores the user's balance, assets,
    transaction history, etc.

    Attributes:
        id: Unique identifier for the wallet, serves as the primary key
        balance: The user's balance in USD
        assets: A dictionary of the user's assets, where the key is the coin ID and the
                value is the quantity of that coin
        time_created: The time the wallet was created, in UNIX time (in seconds)
        status: The status of the wallet, e.g., "active" or "inactive"
        total_current_value: The total value of the user's assets in USD
        owner_id: The user ID of the wallet owner
        transactions: A relationship to the Transaction model, representing the user's
                      transaction history
        value_history: A relationship to the ValueHistory model, storing the history of
                       the wallet's value
    """

    __tablename__ = "wallets"
    __table_args__ = (
        db.CheckConstraint("balance >= 0", name="ck_wallets_balance_nonneg"),
        db.CheckConstraint(
            "reserved_balance >= 0", name="ck_wallets_reserved_balance_nonneg"
        ),
        db.CheckConstraint(
            "balance >= reserved_balance", name="ck_wallets_balance_ge_reserved"
        ),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    balance = db.Column(
        db.Numeric(20, 8),
        default=Decimal("1000000"),
        server_default="1000000",
        nullable=False,
    )
    assets = db.Column(MutableDict.as_mutable(JSONB), default={}, nullable=False)
    reserved_balance = db.Column(
        db.Numeric(20, 8), default=Decimal("0"), nullable=False
    )
    reserved_assets = db.Column(
        MutableDict.as_mutable(JSONB), default=dict, nullable=False
    )
    time_created = db.Column(
        db.Integer, default=lambda: int(time.time()), nullable=False
    )
    status = db.Column(db.Text, default="active", nullable=False)
    total_current_value = db.Column(
        db.Numeric(20, 8), default=Decimal("0"), nullable=False
    )
    owner_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), unique=True)
    transactions = db.relationship("Transaction", backref="wallet", lazy="dynamic")
    value_history = db.relationship("ValueHistory", backref="wallet", uselist=False)

    def __init__(self, owner_id):
        """
        Initializes a new wallet for the specified owner.

        Parameters:
            owner_id (UUID): The UUID of the user who owns this wallet.
        """
        self.owner_id = owner_id

    def update_balance_add(self, amount):
        """Adds a specified amount (USD) to the wallet's balance."""
        self.balance = D(self.balance) + D(amount)

    def update_balance_subtract(self, amount):
        """Subtracts a specified amount (USD) from the wallet's balance."""
        self.balance = D(self.balance) - D(amount)

    def update_assets_add(self, coin_id: str, quantity):
        """Adds a specified quantity of a coin to the wallet's assets."""
        qty_set(self.assets, coin_id, qty_get(self.assets, coin_id) + D(quantity))

    def update_assets_subtract(self, coin_id: str, quantity):
        """
        Subtracts a specified quantity of a coin from the wallet's assets.

        If the resulting holding is dust-or-below, the coin is removed from the
        dictionary so a sell-to-zero doesn't leave a stray key behind.
        """
        remaining = qty_get(self.assets, coin_id) - D(quantity)
        if remaining <= DUST_QTY:
            self.assets.pop(coin_id, None)
        else:
            qty_set(self.assets, coin_id, remaining)

    def has_enough_balance(self, amount):
        """True if the wallet's balance covers the amount (USD)."""
        return D(self.balance) >= D(amount)

    def has_enough_coins(self, coin_id: str, coin_quantity):
        """True if the wallet holds at least coin_quantity of the coin."""
        return qty_get(self.assets, coin_id) >= D(coin_quantity)

    def available_balance(self):
        """Returns the spendable USD balance (total balance minus reserved funds)."""
        return D(self.balance) - D(self.reserved_balance)

    def available_coins(self, coin_id):
        """Returns the spendable quantity of a coin (holdings minus reserved coins)."""
        return qty_get(self.assets, coin_id) - qty_get(self.reserved_assets, coin_id)

    def has_enough_available_balance(self, amount):
        """Returns True if available (unreserved) USD balance covers the amount."""
        return self.available_balance() >= D(amount)

    def has_enough_available_coins(self, coin_id, quantity):
        """Returns True if available (unreserved) holdings cover the quantity."""
        return self.available_coins(coin_id) >= D(quantity)

    def reserve_balance(self, amount):
        """Reserves USD against the wallet for an open buy order."""
        self.reserved_balance = D(self.reserved_balance) + D(amount)

    def release_balance(self, amount):
        """Releases previously reserved USD (clamped at zero)."""
        self.reserved_balance = max(D(0), D(self.reserved_balance) - D(amount))

    def reserve_coins(self, coin_id, quantity):
        """Reserves a quantity of a coin against the wallet for an open sell order."""
        qty_set(
            self.reserved_assets,
            coin_id,
            qty_get(self.reserved_assets, coin_id) + D(quantity),
        )

    def release_coins(self, coin_id, quantity):
        """Releases previously reserved coins, removing the key when it reaches ~0."""
        remaining = qty_get(self.reserved_assets, coin_id) - D(quantity)
        if remaining <= DUST_QTY:
            self.reserved_assets.pop(coin_id, None)
        else:
            qty_set(self.reserved_assets, coin_id, remaining)


class ValueHistory(db.Model):
    """
    ValueHistory model class (for the database) that stores the wallet's total value,
    balance, and assets value over time.

    Attributes:
        id: Unique identifier for the value history, serves as the primary key
        wallet_id: The ID of the wallet to which this value history belongs
        balance_history: A list of the wallet's balance over time
        assets_value_history: A list of the wallet's assets value over time
        total_value_history: A list of the wallet's total value over time
        timestamps: A list of timestamps corresponding to the value history entries
    """

    __tablename__ = "value_histories"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wallet_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("wallets.id"),
        nullable=False,
        unique=True,
    )
    balance_history = db.Column(
        MutableList.as_mutable(ARRAY(db.Numeric(20, 8))),
        default=lambda: [Decimal("1000000")],
    )
    assets_value_history = db.Column(
        MutableList.as_mutable(ARRAY(db.Numeric(20, 8))),
        default=lambda: [Decimal("0")],
    )
    total_value_history = db.Column(
        MutableList.as_mutable(ARRAY(db.Numeric(20, 8))),
        default=lambda: [Decimal("1000000")],
    )
    timestamps = db.Column(
        MutableList.as_mutable(ARRAY(db.Integer)), default=lambda: [int(time.time())]
    )

    def __init__(self, wallet_id):
        """
        Initializes a new ValueHistory instance associated with a specific wallet.

        Parameters:
            wallet_id (UUID): The identifier of the wallet for which the value history
                              is being recorded.
        """
        self.wallet_id = wallet_id

    def update_value_history(self, balance_value, assets_value, time):
        """
        Updates the value history by adding new entries for the balance, assets value,
        and total value at a given time.

        Parameters:
            balance_value (float): The current balance of the wallet to be recorded.
            assets_value (float): The current total value of the assets in the wallet
                                  to be recorded.
            time (int): The UNIX timestamp (in seconds) of when the values were
                        recorded.
        """
        self.balance_history.append(quantize_usd(D(balance_value)))
        self.assets_value_history.append(quantize_usd(D(assets_value)))
        self.total_value_history.append(
            quantize_usd(D(balance_value) + D(assets_value))
        )
        self.timestamps.append(time)


class Transaction(db.Model):
    """
    Transaction model class (for the database) that stores information about a given
    transaction, including the status, type, order type, coin ID, quantity, etc.

    Attributes:
        id: Unique identifier for the transaction, serves as the primary key (UUID)
        status: The status of the transaction ("open" || "finished" || "cancelled")
        transactionType: The type of transaction ("buy" || "sell")
        orderType: The type of order ("market" || "limit" || "stop")
        timestamp: The UNIX timestamp (in seconds) of when the transaction was created
        coin_id: The identifier of the coin involved in the transaction
        quantity: The quantity of the coin involved in the transaction
        price_per_unit: The price per unit of the coin at the time of the transaction
        price_per_unit_at_execution: The price per unit of the coin at the time of
                                     execution (if applicable)
        comment: A comment or note associated with the transaction
        balance_before: The user's balance before the transaction
        balance_after: The user's balance after the transaction
        total_value: The total value of the transaction
        likes: A relationship to the TransactionLikes model, representing the likes
               associated with the transaction
        visibility: A boolean flag indicating whether the transaction is visible to
                    other users
        wallet_id: The ID of the wallet to which this transaction belongs
    """

    __tablename__ = "transactions"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status = db.Column(db.Text, nullable=False)
    transactionType = db.Column(db.Text, nullable=False)
    orderType = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.Integer, default=lambda: int(time.time()), nullable=False)
    coin_id = db.Column(db.Text, nullable=False)
    quantity = db.Column(db.Numeric(38, 18), nullable=False)
    price_per_unit = db.Column(db.Numeric(20, 8), nullable=False)
    price_per_unit_at_execution = db.Column(db.Numeric(20, 8), default=Decimal("-1"))
    comment = db.Column(db.Text)
    balance_before = db.Column(db.Numeric(20, 8), nullable=False)
    balance_after = db.Column(db.Numeric(20, 8))
    total_value = db.Column(db.Numeric(20, 8), nullable=False)
    likes = db.relationship("TransactionLikes", backref="transaction", uselist=False)
    visibility = db.Column(db.Boolean, nullable=False)
    wallet_id = db.Column(UUID(as_uuid=True), db.ForeignKey("wallets.id"))

    def __init__(
        self,
        status,
        transactionType,
        orderType,
        coin_id,
        quantity,
        price_per_unit,
        wallet_id,
        comment,
        balance_before,
        visibility,
    ):
        """Initializes a new instance of the Transaction class with necessary parameters."""
        self.visibility = visibility
        self.status = status
        self.transactionType = transactionType
        self.orderType = orderType
        self.coin_id = coin_id
        self.quantity = quantize_qty(D(quantity))
        self.comment = comment
        self.price_per_unit = quantize_usd(D(price_per_unit))
        self.wallet_id = wallet_id
        self.total_value = quantize_usd(D(quantity) * D(price_per_unit))
        self.balance_before = quantize_usd(D(balance_before))

        if orderType == "market":
            self.price_per_unit_at_execution = quantize_usd(D(price_per_unit))
            if transactionType == "buy":
                self.balance_after = quantize_usd(
                    D(balance_before) - D(quantity) * D(price_per_unit)
                )
            elif transactionType == "sell":
                self.balance_after = quantize_usd(
                    D(balance_before) + D(quantity) * D(price_per_unit)
                )
        elif orderType == "limit" or orderType == "stop":
            self.price_per_unit_at_execution = None
            self.balance_after = None

    def add_like(self, user_id):
        """
        Adds a like to the transaction by a specific user.

        Parameters:
            user_id (UUID): The ID of the user who is liking the transaction.
        """
        self.likes.add_user_like(user_id)

    def remove_like(self, user_id):
        """
        Removes a like from the transaction by a specific user.

        Parameters:
            user_id (UUID): The ID of the user who is unliking the transaction.
        """
        self.likes.remove_user_like(user_id)

    def get_number_of_likes(self):
        """
        Retrieves the total number of likes this transaction has received.

        Returns:
            int: The number of users who have liked the transaction.
        """
        return len(self.likes.liked_by_user_ids)

    def execute_open_order(self, price_per_unit_at_execution):
        """
        Executes an open order at a specified execution price and updates the
        transaction's status and balance.

        Parameters:
            price_per_unit_at_execution (float): The price per unit at which the order
                                                 is executed.
        """
        self.price_per_unit_at_execution = quantize_usd(D(price_per_unit_at_execution))

        if self.transactionType == "buy":
            self.balance_after = quantize_usd(
                D(self.balance_before) - D(self.quantity) * D(price_per_unit_at_execution)
            )
        elif self.transactionType == "sell":
            self.balance_after = quantize_usd(
                D(self.balance_before) + D(self.quantity) * D(price_per_unit_at_execution)
            )
        self.status = "finished"

    def cancel_open_order(self):
        """
        Cancels an open order and sets the user's balance_after to "N/A". Also updates
        the transaction status to "cancelled".
        """
        self.balance_after = Decimal("-1")
        self.status = "cancelled"


class TransactionLikes(db.Model):
    """
    TransactionLikes model class (for the database) that stores the user IDs of users
    who have liked a specific transaction.

    Attributes:
        id: Unique identifier for the transaction likes, serves as the primary key
        liked_by_user_ids: A list of user IDs who have liked the transaction
        transaction_id: The ID of the transaction to which these likes belong
    """

    __tablename__ = "transaction_likes"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    liked_by_user_ids = db.Column(
        MutableList.as_mutable(ARRAY(UUID(as_uuid=True))),
        default=lambda: [],
    )
    transaction_id = db.Column(
        UUID(as_uuid=True), db.ForeignKey("transactions.id"), unique=True
    )

    def __init__(self, transaction_id):
        """
        Initialize a TransactionLikes instance.

        Args:
            transaction_id (UUID): The ID of the transaction to associate likes with.
        """
        self.transaction_id = transaction_id

    def add_user_like(self, user_id):
        """
        Add a user's like to the transaction if they have not already liked it.

        Args:
            user_id (UUID): The ID of the user liking the transaction.
        """
        if user_id not in self.liked_by_user_ids:
            self.liked_by_user_ids.append(user_id)

    def remove_user_like(self, user_id):
        """
        Remove a user's like from the transaction if they have already liked it.

        Args:
            user_id (UUID): The ID of the user whose like is to be removed.
        """
        if user_id in self.liked_by_user_ids:
            self.liked_by_user_ids.remove(user_id)


class TokenBlocklist(db.Model):
    """
    TokenBlocklist model class (for the database) that stores the JTIs of revoked JWTs.

    A token is revoked when its JTI is present in this table. The blocklist is consulted
    by the @jwt.token_in_blocklist_loader callback on every @jwt_required() request, so
    any revoked access or refresh token is rejected. Refresh tokens are added here when
    they are rotated (on /refresh) or invalidated (on /logout).

    Attributes:
        id: Unique identifier for the blocklist entry, serves as the primary key
        jti: The unique identifier (JTI claim) of the revoked token
        token_type: The type of the revoked token (e.g. "access" or "refresh")
        user_id: The ID of the user the token was issued to (supports bulk revocation)
        created_at: Unix timestamp of when the token was revoked
        expires_at: Unix timestamp of the token's natural expiry (for later cleanup)
    """

    __tablename__ = "token_blocklist"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    jti = db.Column(db.String(36), nullable=False, unique=True, index=True)
    token_type = db.Column(db.String(16), nullable=True)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), index=True)
    created_at = db.Column(db.Integer, nullable=False)
    expires_at = db.Column(db.Integer, nullable=True)

    def __init__(self, jti, token_type=None, user_id=None, expires_at=None):
        """
        Initialize a TokenBlocklist instance.

        Args:
            jti (str): The JTI claim of the token being revoked.
            token_type (str): The type of token (e.g. "access" or "refresh").
            user_id (UUID): The ID of the user the token was issued to.
            expires_at (int): Unix timestamp of the token's natural expiry.
        """
        self.jti = jti
        self.token_type = token_type
        self.user_id = user_id
        self.created_at = int(time.time())
        self.expires_at = expires_at
