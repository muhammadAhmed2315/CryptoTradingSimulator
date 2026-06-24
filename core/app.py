from flask_jwt_extended import verify_jwt_in_request
from constants import NEWSDATA_API_KEY
import logging
import math
import time
from datetime import datetime

import requests
from flask import (
    Blueprint,
    jsonify,
    request,
    session,
)
from flask_jwt_extended import get_jwt_identity
from flask_login import current_user
from sqlalchemy import and_, or_
from sqlalchemy.orm import joinedload

from constants import (
    COINGECKO_API_HEADERS,
    OPEN_TRADE_UPDATE_INTERVAL_SECONDS,
    WALLET_VALUE_UPDATE_INTERVAL_SECONDS,
)
from extensions import db
from models import Transaction, TransactionLikes, User, Wallet
from money import D, qty_get
from RedditScraper.RedditScraper import RedditScraper

core = Blueprint("core", __name__)

_COINS_LIST_CACHE = {"data": None, "fetched_at": 0}
_COINS_LIST_CACHE_SET = set()
COINS_LIST_CACHE_TTL_SECONDS = 600  # 10 minutes


def _lock_wallet(wallet_id):
    """Load and row-lock a wallet within the current transaction (SELECT ... FOR UPDATE)."""
    return db.session.scalar(
        db.select(Wallet).filter_by(id=wallet_id).with_for_update()
    )


def get_coins_list_cached():
    """Returns the full CoinGecko /coins/list as a Python list, cached for COINS_LIST_CACHE_TTL_SECONDS."""
    global _COINS_LIST_CACHE_SET
    try:
        now = int(time.time())
        if (
            _COINS_LIST_CACHE["data"] is not None
            and now - _COINS_LIST_CACHE["fetched_at"] < COINS_LIST_CACHE_TTL_SECONDS
        ):
            return _COINS_LIST_CACHE["data"]
        url = "https://api.coingecko.com/api/v3/coins/list"
        response = requests.get(url, headers=COINGECKO_API_HEADERS, timeout=10)
        data = response.json()

        _COINS_LIST_CACHE["data"] = data
        _COINS_LIST_CACHE["fetched_at"] = now
        _COINS_LIST_CACHE_SET = set([coin["id"] for coin in data])
        return data
    except Exception:
        return jsonify({"error": "Internal server error"}), 502


# Verify user has a username
@core.before_request
def require_username():
    if request.method == "OPTIONS":
        return None

    verify_jwt_in_request()
    user = db.session.get(User, get_jwt_identity())
    if user is None or not user.username:
        return jsonify({"error": "username required"}), 403


@core.route("/get_trade_filter_counts", methods=["GET"])
def get_trade_filter_counts():
    try:
        # Get current user
        user_id = get_jwt_identity()
        user = User.query.filter_by(id=user_id).first()

        # Count transactions by status in SQL
        base = user.wallet.transactions
        filterCounts = {
            "all": base.count(),
            "open": base.filter_by(status="open").count(),
            "finished": base.filter_by(status="finished").count(),
            "cancelled": base.filter_by(status="cancelled").count(),
        }

        return (
            jsonify(filterCounts),
            200,
        )
    except Exception:
        logging.exception("Failed to fetch trade counts by filters")
        return (
            jsonify({"error": "Internal server error"}),
            500,
        )


@core.route("/get_trades_info", methods=["POST"])
def get_trades_info():
    """
    Fetches the transaction history for the currently logged-in user and returns (as a
    JSON response) a paginated list of transactions sorted according to the specified
    criteria.

    Each page contains up to 10 transactions, and if a user has no transactions an
    empty list is returned. The response also includes the total number of pages based
    on the number of transactions.

    The possible types of sorts are specified in the sort_transactions() function.

    Since the CoinGecko API only updates coin prices every 45 seconds, this function
    only triggers a background update of the user's wallet ValueHistory attribute if
    the user has not visited the page within the last 45 seconds. If it has been more
    than 45 seconds since the user's last visit, it triggers a background update of the
    user's wallet ValueHistory attribute using the CoinGecko API. The last visit
    timestamp is stored in the session and updated upon each visit.

    Args:
        None. Expects JSON data in the request body with the following keys:
        - page (int): The page number of the transactions to fetch (1-indexed).
        - TODO: filter

    Returns:
        Response (JSON): A JSON object containing:
        - success (str): A message indicating the success of the operation.
        - data (list): A list of transaction details, or an empty list if no
                       transactions exist for the current user.
        - maxPages (int): The total number of pages for pagination.

    Raises:
        KeyError: If the 'page' or 'sort' keys are not found in the request data.
    """
    try:
        # Get current user
        user_id = get_jwt_identity()
        user = User.query.filter_by(id=user_id).first()

        # Update last visit
        last_visit = session.get("my_trades_last_visited")
        if last_visit is not None:
            if int(time.time()) - last_visit >= 45:
                update_user_wallet_value_in_background(user.wallet.id)
        session["my_trades_last_visited"] = int(time.time())

        # Parse request body
        data = request.get_json()
        page = data["page"]
        dataFilter = data["filter"]

        # Build the (optionally filtered) transactions query in SQL
        filtered_query = user.wallet.transactions
        if dataFilter != "all":
            filtered_query = filtered_query.filter_by(status=dataFilter)

        # Total for pagination
        total_count = filtered_query.count()

        # If the user has transactions, paginate the results in SQL
        if total_count:
            max_pages = math.ceil(total_count / 10)

            # Fetch only the requested page, most recent first
            page_transactions = (
                filtered_query.order_by(Transaction.timestamp.desc())
                .offset((page - 1) * 10)
                .limit(10)
                .all()
            )

            res = []

            # Extract the necessary data from each transaction
            for transaction in page_transactions:
                temp = {}
                temp["orderType"] = transaction.orderType
                temp["transactionType"] = transaction.transactionType
                temp["id"] = transaction.id
                temp["timestamp"] = transaction.timestamp
                temp["coin_id"] = transaction.coin_id
                temp["quantity"] = transaction.quantity
                temp["price_per_unit"] = transaction.price_per_unit
                temp["comment"] = transaction.comment
                temp["status"] = transaction.status
                temp["price_at_execution"] = transaction.price_per_unit_at_execution
                res.append(temp)

            all_coins = get_coins_list_cached()
            all_coins_dict = {}
            for coin in all_coins:
                all_coins_dict[coin["id"]] = coin["symbol"]

            for transaction in res:
                transaction["ticker"] = all_coins_dict[transaction["coin_id"]]

            return (
                jsonify(
                    {
                        "data": res,
                        "maxPages": max_pages,
                    }
                ),
                200,
            )
        else:
            return (
                jsonify(
                    {
                        "data": [],
                        "maxPages": 0,
                    },
                ),
                200,
            )
    except Exception:
        logging.exception("Failed to fetch trade history")
        return (
            jsonify({"error": "Internal server error"}),
            500,
        )


@core.route("/get_feedposts", methods=["POST"])
def get_feedposts():
    """
    Fetches and returns a list of feed posts (transactions) based on the specified type
    and pagination.

    This function retrieves the information about the globally visible transactions or
    transactions specific to the current user (based on the input type). The
    transactions are sorted by their timestamp in descending order to ensure the most
    recent transactions are shown first.

    Args:
        None directly. Expects a JSON payload in the request containing:
            type (str): Can be 'GLOBAL' = fetch transactions visible to all users or
                        'PRIVATE' = fetch transactions specific to the current user.
            page (int): The page number for pagination purposes, used to calculate the
                        slice of transactions to return (0-indexed).

    Returns:
        A JSON response containing:
            - 'success': A message indicating the status of the request.
            - 'data': A list of dictionaries, each representing a transaction with
                      details such as transaction ID, username, timestamp, number of
                      likes, coin ID, quantity, price per unit, transaction type, and
                      order type.
    """
    try:
        data = request.get_json()
        type = data["type"]
        page = data["page"]

        PAGE_SIZE = 10

        # Fetch the current user
        user_id = get_jwt_identity()
        user = User.query.filter_by(id=user_id).first()

        if type == "GLOBAL":
            base_query = Transaction.query.filter_by(visibility=True).order_by(
                Transaction.timestamp.desc()
            )
        elif type == "PRIVATE":
            base_query = Transaction.query.filter_by(wallet_id=user.wallet.id).order_by(
                Transaction.timestamp.desc()
            )
        else:
            return jsonify({"error": "Invalid feed type"}), 400

        # Eager-load the relationships accessed per post to avoid N+1 queries
        base_query = base_query.options(
            joinedload(Transaction.wallet).joinedload(Wallet.owner),
            joinedload(Transaction.likes),
        )

        total = base_query.count()
        max_pages = max(math.ceil(total / PAGE_SIZE) - 1, 0)
        transactions = base_query.offset(page * PAGE_SIZE).limit(PAGE_SIZE).all()

        res = []

        for transaction in transactions:
            temp = {}

            # Guard against transactions without a TransactionLikes row
            likes_obj = transaction.likes
            num_likes = len(likes_obj.liked_by_user_ids) if likes_obj else 0
            curr_user_liked = bool(likes_obj and user.id in likes_obj.liked_by_user_ids)

            temp["id"] = transaction.id
            temp["username"] = transaction.wallet.owner.username
            temp["timestamp"] = transaction.timestamp
            temp["comment"] = transaction.comment
            temp["likes"] = num_likes
            temp["coin_id"] = transaction.coin_id
            temp["quantity"] = transaction.quantity
            temp["price_per_unit"] = transaction.price_per_unit
            temp["transaction_type"] = transaction.transactionType
            temp["order_type"] = transaction.orderType

            # Has user liked the current transaction
            temp["curr_user_liked"] = curr_user_liked

            res.append(temp)

        return (
            jsonify(
                {
                    "data": res,
                    "nextPage": page + 1 if page < max_pages else None,
                }
            ),
            200,
        )
    except Exception as e:
        return (
            jsonify({"error": "Failed to fetch feed posts due to internal error"}),
            500,
        )


@core.route("/update_likes", methods=["POST"])
def update_likes():
    """
    Updates the like count for a specific transaction.

    Given a transaction ID and a boolean flag indicating whether to add or remove a
    like from the transaction, the function adds or removes the current user from the
    transaction's TransactionLikes.liked_by_user_ids list.
    """
    try:
        data = request.get_json() or {}
        is_increment = data.get("isIncrement")
        transaction_id = data.get("transactionID")

        # Validate that a transaction ID was provided
        if transaction_id is None:
            return jsonify({"error": "Missing transaction ID"}), 400

        # Get the current user
        user_id = get_jwt_identity()
        current_user = User.query.filter_by(id=user_id).first()

        # Verify that the current user exists
        if current_user is None:
            return jsonify({"error": "Transaction not found"}), 404

        # Get the transaction object from the database
        transaction = db.session.get(Transaction, transaction_id)

        # Guard against an unknown/missing transaction
        if transaction is None or (
            not transaction.visibility
            and transaction.wallet_id != current_user.wallet.id
        ):
            return jsonify({"error": "Transaction not found"}), 404

        # Guard against a transaction without a TransactionLikes row by creating one
        if transaction.likes is None:
            likes = TransactionLikes(transaction_id=transaction.id)
            db.session.add(likes)
            db.session.flush()

        # Increment or decrement the number of likes for the transaction
        if is_increment:
            transaction.add_like(current_user.id)
        else:
            transaction.remove_like(current_user.id)

        # Save the updated transaction to the database
        db.session.add(transaction)
        db.session.add(transaction.likes)
        db.session.commit()
    except Exception:
        logging.exception("Failed to update like count")
        return (
            jsonify({"error": "Internal server error"}),
            500,
        )

    return (
        jsonify(
            {
                "success": "Like count successfully updated",
                "currLikes": transaction.get_number_of_likes(),
            },
        ),
        200,
    )


@core.route("/process_order", methods=["POST"])
def process_order():
    """
    Processes a cryptocurrency transaction submitted via a POST request containing JSON
    data.

    The function validates the input JSON for necessary fields and constraints, such as
    ensuring positive quantities and sufficient balances to complete buy or sell
    orders. It handles different transaction types and order types accordingly.

    If the transaction is valid:
    - It updates the user's wallet balance and assets based on the transaction type and
      order type.
    - It records the transaction in the database along with a new TransactionLikes
      object to track likes.
    - It invokes a background task to update the wallet value if necessary.

    Returns:
        JSON response: A JSON object indicating the success or failure of the
                       transaction. On success, returns HTTP 201. On failure due to
                       client errors (e.g., missing data, insufficient funds), returns
                       HTTP 400. On failure due to server errors (e.g., database
                       issues), returns HTTP 500.

    Raises:
        HTTPException: If the input JSON is missing or incorrectly formatted, or if any
                       data constraints are violated, the function will raise an HTTP
                       exception with an appropriate status code and error message.
    """
    # Ensure request contains necessary JSON data
    data = request.get_json()

    # Check data contains all required fields
    required_fields = {
        "transactionType",
        "orderType",
        "quantity",
        "coin_id",
        "comment",
        "visibility",
        "price_per_unit",
    }

    # Verify required fields have been provided
    for key in required_fields:
        if key not in data:
            return jsonify({"error": "Invalid order request"}), 422

    # Validate data properties are correct
    if data["orderType"] not in ["market", "limit", "stop"] or data[
        "transactionType"
    ] not in ["buy", "sell"]:
        return jsonify({"error": "Invalid order request"}), 422

    # Make sure user did not enter an invalid quantity
    if (
        not isinstance(data["quantity"], (int, float))
        or isinstance(data["quantity"], bool)
        or not math.isfinite(data["quantity"])
        or data["quantity"] <= 0
    ):
        return (
            jsonify({"error": "Quantity must be a positive number"}),
            422,
        )

    # Validate correct trigger/quoted price
    if (
        not isinstance(data["price_per_unit"], (int, float))
        or isinstance(data["price_per_unit"], bool)
        or not math.isfinite(data["price_per_unit"])
        or data["price_per_unit"] <= 0
    ):
        return (
            jsonify({"error": "Trigger/quoted price must be a positive number"}),
            422,
        )

    # All money/quantity math below runs in Decimal. Convert the validated request
    # values once here so the float values from JSON never enter the ledger.
    data["quantity"] = D(data["quantity"])
    data["price_per_unit"] = D(data["price_per_unit"])

    # Validate that the coin exists and get its current price
    try:
        market_data = get_coins_data(data["coin_id"])
        raw_price = market_data[0]["current_price"]
    except (requests.RequestException, IndexError, KeyError, TypeError, ValueError):
        return (
            jsonify({"error": "Invalid coin_id. Coin does not exist."}),
            422,
        )

    # CoinGecko can return a null/zero price for unknown or delisted coins. Validate
    # the raw number, then convert into the Decimal ledger domain.
    if (
        not isinstance(raw_price, (int, float))
        or isinstance(raw_price, bool)
        or not math.isfinite(raw_price)
        or raw_price <= 0
    ):
        return (
            jsonify({"error": "No valid market price for this coin"}),
            422,
        )

    current_price = D(raw_price)

    # Validate unfavourable slippage tolerance for market orders
    if data["orderType"] == "market":
        if (
            data["transactionType"] == "buy"
            and ((current_price - data["price_per_unit"]) / data["price_per_unit"])
            > 0.005
        ):
            return (
                jsonify(
                    {"error": "Slippage tolerance exceeded (0.5%). Please refresh."}
                ),
                409,
            )
        elif (
            data["transactionType"] == "sell"
            and ((current_price - data["price_per_unit"]) / data["price_per_unit"])
            < -0.005
        ):
            return (
                jsonify(
                    {"error": "Slippage tolerance exceeded (0.5%). Please refresh."}
                ),
                409,
            )

    # A limit/stop trigger must sit on the meaningful side of the current market
    # price; otherwise the order would fill on the next executor pass at ~the
    # current price (defeating the point of the order type). The frontend enforces
    # this, but validate server-side too so a direct API client can't place a
    # wrong-side order.
    trigger_side_error = None
    if data["orderType"] == "limit" and data["transactionType"] == "buy":
        if data["price_per_unit"] > current_price:
            trigger_side_error = (
                "A limit buy price must be at or below the current market price."
            )
    elif data["orderType"] == "limit" and data["transactionType"] == "sell":
        if data["price_per_unit"] < current_price:
            trigger_side_error = (
                "A limit sell price must be at or above the current market price."
            )
    elif data["orderType"] == "stop" and data["transactionType"] == "buy":
        if data["price_per_unit"] < current_price:
            trigger_side_error = (
                "A stop buy price must be at or above the current market price."
            )
    elif data["orderType"] == "stop" and data["transactionType"] == "sell":
        if data["price_per_unit"] > current_price:
            trigger_side_error = (
                "A stop sell price must be at or below the current market price."
            )

    if trigger_side_error:
        return jsonify({"error": trigger_side_error}), 422

    # Get the current user
    user_id = get_jwt_identity()
    user = User.query.filter_by(id=user_id).first()

    # Make sure user has enough balance (USD) to execute a buy order of any type
    if data["transactionType"] == "buy":
        if not user.wallet.has_enough_balance(
            data["quantity"] * data["price_per_unit"]
        ):
            return (
                jsonify({"error": "Order failed: insufficient USD balance"}),
                400,
            )

    # Make sure user is not selling more coins than they own for any type of sell order
    elif data["transactionType"] == "sell":
        if not user.wallet.has_enough_coins(data["coin_id"], data["quantity"]):
            return (
                jsonify({"error": "Order failed: Insufficient coion balance"}),
                400,
            )

    # Get status value
    status = "finished" if data["orderType"] == "market" else "open"

    # Save the transaction in the database
    transaction = Transaction(
        status=status,
        transactionType=data["transactionType"],
        orderType=data["orderType"],
        coin_id=data["coin_id"],
        quantity=data["quantity"],
        price_per_unit=(
            current_price if data["orderType"] == "market" else data["price_per_unit"]
        ),
        wallet_id=user.wallet.id,
        comment=data["comment"],
        balance_before=user.wallet.balance,
        visibility=data["visibility"],
    )

    try:
        # Acquire a row-level lock on the wallet so the authoritative funds/holdings
        # check and the subsequent mutation happen atomically (prevents TOCTOU races
        # against concurrent orders or the background executor). The pre-lock checks
        # above are only a cheap fast-fail; the checks below are authoritative.
        user_wallet = _lock_wallet(user.wallet.id)
        if user_wallet is None:
            db.session.rollback()
            return jsonify({"error": "Wallet not found"}), 404

        if transaction.orderType == "market" and transaction.transactionType == "buy":
            # Re-validate against the locked wallet before mutating
            if not user_wallet.has_enough_available_balance(
                transaction.quantity * transaction.price_per_unit
            ):
                db.session.rollback()
                return (
                    jsonify({"error": "Order failed: insufficient USD balance"}),
                    400,
                )

            # Record the balance as read under the lock
            transaction.balance_before = user_wallet.balance

            # Update wallet balance
            user_wallet.update_balance_subtract(
                transaction.quantity * transaction.price_per_unit
            )

            # Update wallet assets dictionary
            user_wallet.update_assets_add(transaction.coin_id, transaction.quantity)
        elif (
            transaction.orderType == "market" and transaction.transactionType == "sell"
        ):
            # Re-validate against the locked wallet before mutating
            if not user_wallet.has_enough_available_coins(
                transaction.coin_id, transaction.quantity
            ):
                db.session.rollback()
                return (
                    jsonify({"error": "Order failed: insufficient coin balance"}),
                    400,
                )

            # Record the balance as read under the lock
            transaction.balance_before = user_wallet.balance

            # Update wallet balance
            user_wallet.update_balance_add(
                transaction.quantity * transaction.price_per_unit
            )

            # Update wallet assets dictionary
            user_wallet.update_assets_subtract(
                transaction.coin_id, transaction.quantity
            )
        elif transaction.status == "open":
            # Placing an open limit/stop order: reserve the funds (buy) or coins
            # (sell) against the locked wallet so they cannot be double-spent by
            # other orders before this one fills. Reservation is computed at the
            # TRIGGER price (transaction.price_per_unit) and validated against the
            # AVAILABLE (unreserved) balance/holdings; the pre-lock checks above are
            # only a looser total-funds fast-fail and do not reserve.
            if transaction.transactionType == "buy":
                required = transaction.quantity * transaction.price_per_unit
                if not user_wallet.has_enough_available_balance(required):
                    db.session.rollback()
                    return (
                        jsonify(
                            {
                                "error": "Order failed: insufficient available USD balance"
                            }
                        ),
                        400,
                    )
                user_wallet.reserve_balance(required)
            elif transaction.transactionType == "sell":
                if not user_wallet.has_enough_available_coins(
                    transaction.coin_id, transaction.quantity
                ):
                    db.session.rollback()
                    return (
                        jsonify(
                            {
                                "error": "Order failed: insufficient available coin balance"
                            }
                        ),
                        400,
                    )
                user_wallet.reserve_coins(transaction.coin_id, transaction.quantity)

        # Add transaction and update user_wallet, then flush to assign the
        # transaction's primary key without committing yet.
        db.session.add(transaction)
        db.session.add(user_wallet)
        db.session.flush()

        # Create the likes row and commit everything in a single transaction, so a
        # failure can't leave an orphan transaction with no TransactionLikes row.
        transaction_likes = TransactionLikes(transaction_id=transaction.id)
        db.session.add(transaction_likes)
        db.session.commit()

        update_user_wallet_value_in_background(user_wallet.id)

        return jsonify({"success": "Transaction processed successfully"}), 201
    except Exception:
        db.session.rollback()
        logging.exception("Order processing failed")
        return (
            jsonify({"error": "Internal server error"}),
            500,
        )


@core.route("/get_wallet_history", methods=["GET"])
def get_wallet_history():
    """
    Retrieves the wallet value history for the currently logged-in user.

    This function, upon a successful request, returns a JSON response containing the
    wallet value history, including balance, assets value, total value, and timestamps.

    If the wallet value history could not be fetched from the database for any reason,
    the function returns an error JSON response.

    Raises:
        Exception: If any error occurs during the retrieval process.
    """
    try:
        user_id = get_jwt_identity()
        user = User.query.filter_by(id=user_id).first()
        wallet_history = user.wallet.value_history

        res = {
            "balance": [
                [wallet_history.timestamps[i], wallet_history.balance_history[i]]
                for i in range(len(wallet_history.timestamps))
            ],
            "assets": [
                [wallet_history.timestamps[i], wallet_history.assets_value_history[i]]
                for i in range(len(wallet_history.timestamps))
            ],
            "totalValue": [
                [wallet_history.timestamps[i], wallet_history.total_value_history[i]]
                for i in range(len(wallet_history.timestamps))
            ],
        }

        return jsonify(res), 200
    except Exception as e:
        return (
            jsonify(
                {
                    "error": "An error occurred while retrieving the wallet history from the database"
                }
            ),
            500,
        )


@core.route("/get_wallet_total_current_value", methods=["GET"])
def get_wallet_total_current_value():
    """
    Retrieves the current total value of the current user's wallet (i.e., returns
    wallet.get_wallet_total_current_value, which represents the total value of all
    coins the user owns, plus their balance in USD).

    This function, upon a successful request, returns a JSON response containing the
    wallet total current value.

    If the wallet value history could not be fetched from the database for any reason,
    the function returns an error JSON response.

    Raises:
        Exception: If any error occurs during the retrieval process.
    """
    # Since the CoinGecko API only updates coin prices every 45 seconds, only call the
    # update wallet history function if this page was accessed 45 seconds or more ago
    try:
        # Get the current user
        user_id = get_jwt_identity()
        user = User.query.filter_by(id=user_id).first()

        last_visit = session.get("get_wallet_total_current_value_last_visited")
        if last_visit is not None:
            if int(time.time()) - last_visit >= 45:
                update_user_wallet_value_in_background(user.wallet.id)
        session["get_wallet_total_current_value_last_visited"] = int(time.time())

        current_total_value = user.wallet.total_current_value
        return jsonify(current_total_value), 200

    except Exception:
        logging.exception("Could not get user's total portfolio value")
        return (
            jsonify({"error": "Internal server error"}),
            500,
        )


@core.route("/get_news_articles", methods=["POST"])
def get_news_articles():
    """
    Fetch news articles based on a user-specified query and page number.

    This endpoint accepts a JSON payload with the search query and page number to fetch
    news articles using the NewsData API.

    Returns:
        json: A JSON object containing a success message and a list of news articles.
              Each article includes details like the title, URL, UNIX timestamp,
              description, and publisher.
    """
    try:
        data = request.get_json()
        query = data["query"]
        next_page = data["nextPage"]

        params = {
            "apikey": NEWSDATA_API_KEY,
            "q": query,
            "removeduplicate": 1,
            "language": "en",
        }

        if next_page != "":
            params["page"] = next_page

        response = requests.get(
            "https://newsdata.io/api/1/crypto", params=params, timeout=10
        )
        data = response.json()

        if data["status"] == "error":
            if data["results"]["code"] == "RateLimitExceeded":
                return (
                    jsonify(
                        {
                            "error": "News service rate limit reached. Please try again in a few minutes."
                        }
                    ),
                    429,
                )
            return (
                jsonify(
                    {"error": "Failed to fetch news articles. Please try again later."}
                ),
                502,
            )

        return jsonify(data), 200
    except Exception:
        logging.exception("Failed to fetch news")
        return jsonify({"error": "Internal server error"}), 500


# Module-level cache for the RedditScraper instance. The scraper performs a blocking
# OAuth token POST in its constructor, and the resulting token is valid for ~1 hour, so
# we reuse a single instance across requests and only recreate it once its token is
# close to expiring.
_reddit_scraper = None


def get_reddit_scraper():
    """Returns a cached RedditScraper instance, recreating it (and its OAuth token)
    only when no valid cached token is available."""
    global _reddit_scraper
    if _reddit_scraper is None or not _reddit_scraper.is_token_valid():
        _reddit_scraper = RedditScraper()
    return _reddit_scraper


@core.route("/get_reddit_posts", methods=["POST"])
def get_reddit_posts():
    """
    Fetches posts from Reddit based on a user-specified query and pagination parameter.

    This endpoint accepts a JSON payload with the search query and pagination 'after'
    parameter to fetch posts. It uses the RedditScraper class to scrape Reddit posts
    based on relevance within the past week.

    Returns:
        json: A JSON object containing the success message and a list of posts. Each
              post includes details like title, thumbnail, content, subreddit, score,
              comment count, id, url, fullname, and a human-readable timestamp
              indicating how long ago the post was made.
    """
    try:
        data = request.get_json()
        query = data["query"]
        after = data["after"]

        # Reuse a cached RedditScraper (and its OAuth token) until the token is
        # close to expiring, instead of doing a fresh token POST on every request.
        scraper = get_reddit_scraper()

        # Search for Reddit posts
        posts = scraper.search_keyword_in_reddit(
            sort="relevance", keyword=query, time="week", limit=10, after=after
        )

        res = []

        # Extract the necessary data from each post
        for post in posts:
            temp = {}
            temp["title"] = post.title
            temp["thumbnail"] = post.thumbnail if post.thumbnail != "self" else ""
            temp["content"] = post.content
            temp["subreddit"] = post.subreddit
            temp["score"] = post.score
            temp["comment_count"] = post.comment_count
            temp["id"] = post.id
            temp["url"] = post.url
            temp["fullname"] = post.fullname
            temp["timestamp"] = time_ago(post.timestamp)
            res.append(temp)

        return jsonify(res), 200
    except Exception:
        logging.exception("Failed to fetch Reddit posts")
        return jsonify({"error": "Internal server error"}), 500


def time_ago(unix_timestamp):
    """
    Converts a UNIX timestamp to a relative time string indicating how long ago that
    time was in a human-readable format.

    Parameters:
        unix_timestamp (int): A UNIX timestamp in seconds.

    Returns:
        str: A string representing the time difference in a human-readable format, such
             as "Just now", "X minutes ago", "X hours ago", or "X days ago". It
             dynamically adjusts the singular or plural form based on the time
             difference.

    Examples:
        - If the timestamp is less than a minute ago, it returns "Just now".
        - If the timestamp is one minute ago, it returns "1 minute ago".
        - If the timestamp is several hours or days in the past, it formats the string
          accordingly.
    """
    now = datetime.now()
    past_time = datetime.fromtimestamp(unix_timestamp)
    difference = now - past_time

    seconds = difference.total_seconds()

    if seconds < 60:
        return "Just now"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
    elif seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    else:
        days = int(seconds // 86400)
        return f"{days} day{'s' if days > 1 else ''} ago"


def update_user_wallet_value_in_background(current_wallet_id=None):
    """
    Updates the value of user wallets in the background by fetching the latest market
    prices of owned cryptocurrencies from the CoinGecko API and recalculating the total
    asset values for each wallet. If a specific wallet ID is provided, only that
    wallet's value is updated; otherwise, the function updates all wallets in the
    database.

    - Retrieves a list of all unique cryptocurrency coins owned by all registered
      users.
    - Fetches the current market prices for these coins from the CoinGecko API, in
      batches of up to 250 coins at a time to adhere to the API rate limits.
    - Updates the balance value history, assets value history, total value history,
      total current value, and timestamp for each wallet.
    - If more than 250 coins need to be fetched, the function sleeps for 25 seconds
      between API calls to avoid rate-limiting.
    - If no wallet ID is provided (i.e., the function is updating the wallet value
      history for all wallets in the database), the function sleeps for 30 minutes
      (1800 seconds) before executing again, in order to control the frequency of
      updates.

    Args:
        current_wallet_id (UUID, optional): The ID of a specific wallet to update.
                                           If None, all wallets are updated.
    """
    while True:
        from app import app

        start = time.monotonic()

        with app.app_context():
            coins = set()
            coin_market_prices = {}

            try:
                if current_wallet_id:
                    all_wallets = [db.session.query(Wallet).get(current_wallet_id)]
                else:
                    # Get list of all coins currently owned by users
                    all_wallets = Wallet.query.all()
            except Exception:
                db.session.rollback()
                logging.exception("Failed to load wallets for value update")
                # Empty list: the loop below no-ops, then the background path
                # sleeps and retries next cycle while the synchronous
                # (current_wallet_id) path breaks out — no busy-spin, no hang.
                all_wallets = []

            for wallet in all_wallets:
                coins.update(set(wallet.assets.keys()))

            coins = list(coins)

            # Iterate over 250 coins at a time, getting their market data
            # 250 because the CoinGecko API only allows fetching market data of 250 coins
            # at a time
            current_time = int(time.time())
            for i in range(0, len(coins), 250):
                current_batch = coins[i : i + 250]
                current_batch = ",".join(current_batch)

                try:
                    url = "https://api.coingecko.com/api/v3/coins/markets"
                    params = {
                        "vs_currency": "usd",
                        "per_page": 250,
                        "ids": current_batch,
                    }
                    response = requests.get(
                        url, params=params, headers=COINGECKO_API_HEADERS, timeout=10
                    )
                    data = response.json()

                    for coin in data:
                        coin_market_prices[coin["id"]] = D(coin["current_price"])
                except Exception:
                    continue

                # If more than one page of data needs to be fetched from the API, then
                # sleep for 25 seconds before making another request so that we don't get
                # rate-limited by the API
                if i + 250 < len(coins):
                    time.sleep(25)

            # Update the following fields for each wallet:
            # - balance_value_history
            # - assets_value_history
            # - total_value_history
            # - total_current_value
            try:
                for wallet in all_wallets:
                    # If we failed to fetch a price for any coin this wallet holds
                    # (e.g. a CoinGecko 429 left a batch unpopulated), skip the
                    # wallet this cycle. Otherwise the missing coins would be
                    # silently valued at $0 and that falsely-low total would be
                    # persisted to the value history.
                    missing = [
                        key
                        for key in wallet.assets
                        if qty_get(wallet.assets, key) and key not in coin_market_prices
                    ]
                    if missing:
                        logging.warning(
                            "Skipping value update for wallet %s: missing prices for %s",
                            wallet.id,
                            missing,
                        )
                        continue

                    # Get current total value of assets
                    curr_assets_value = D(0)
                    for key in wallet.assets:
                        if key in coin_market_prices:
                            curr_assets_value += (
                                qty_get(wallet.assets, key) * coin_market_prices[key]
                            )

                    wallet.value_history.update_value_history(
                        wallet.balance, curr_assets_value, current_time
                    )

                    wallet.total_current_value = wallet.balance + curr_assets_value

                    db.session.add(wallet)
                db.session.commit()
            except Exception:
                db.session.rollback()
                logging.exception("Failed to update wallet value history")

        if not current_wallet_id:
            elapsed = time.monotonic() - start
            time.sleep(max(0, WALLET_VALUE_UPDATE_INTERVAL_SECONDS - elapsed))
        else:
            break


def update_open_trades_in_background():
    """
    Continuously monitors and executes open trades based on current market conditions.

    This function runs in a infinite loop that checks all open trades for all users and
    determines if they can be executed based on their type (limit or stop) and the
    current market price of the coin involved. Trades are executed (if the user has
    enough money/balance) or cancelled (if the user does not have enough money/balance),
    updating the transaction and wallet accordingly.


    This function fetches current market prices in batches to adhere to API rate limits
    and updates each trade accordingly.

    This function should be run in a background thread or as a separate process due
    to its infinite loop nature and sleep intervals which pause execution to limit
    API calls and database transactions.

    The function uses two helper functions:
    - `cancel_open_order`: Cancels an open order and updates the transaction without
                           committing it.
    - `execute_open_order`: Executes an open order based on market prices and updates
                            balances and assets.
    """

    def cancel_open_order(transaction, wallet):
        """
        Cancels an open order/transaction and updates the transaction in the database
        (does not commit the changes to the database)

        This function invokes the `cancel_open_order` method of the given transaction
        object to change its status to 'cancelled', releases the funds/coins that were
        reserved when the order was placed (back onto the row-locked wallet), and adds
        both the transaction and wallet to the database session for persistence.

        Args:
        transaction (Transaction): The transaction object representing the order to be cancelled.
        wallet (Wallet): The row-locked wallet instance whose reservation should be released.

        Returns:
        None
        """
        # Release the reservation held at placement (computed at the trigger price)
        if transaction.transactionType == "buy":
            wallet.release_balance(transaction.quantity * transaction.price_per_unit)
        else:
            wallet.release_coins(transaction.coin_id, transaction.quantity)

        transaction.cancel_open_order()
        db.session.add(transaction)
        db.session.add(wallet)

    def execute_open_order(transaction, is_buy, wallet):
        """
        Executes an open order based on the current market price and updates the wallet
        balance and assets.

        This function checks if the order is a buy or sell. For a buy order, it
        subtracts the total cost of the coins from the wallet's balance and adds the
        quantity to the assets. For a sell order, it adds the total cost to the balance
        and subtracts the quantity from the assets. The transaction and wallet states
        are then updated in the database session.

        Args:
        transaction (Transaction): The transaction object representing the order to be
                                   executed.
        is_buy (bool): A flag indicating whether the transaction is a buy (True) or
                       sell (False).
        wallet (Wallet): The row-locked wallet instance to mutate.

        Returns:
        None
        """
        # Execute the order. transaction.execute_open_order sets
        # price_per_unit_at_execution and leaves price_per_unit (the trigger price)
        # untouched, so it is safe to use price_per_unit below for the release.
        transaction.execute_open_order(coin_market_prices[transaction.coin_id])
        if is_buy:
            # Release the USD reserved at placement (trigger cost), then apply the
            # actual fill cost at the market price. reserved-at-trigger >= actual
            # fill cost for a buy limit/stop, so available balance is restored.
            wallet.release_balance(transaction.quantity * transaction.price_per_unit)
            # Update wallet balance and assets for buy order
            wallet.update_balance_subtract(
                transaction.quantity * coin_market_prices[transaction.coin_id]
            )
            wallet.update_assets_add(transaction.coin_id, transaction.quantity)
        else:
            # Release the coins reserved at placement BEFORE subtracting the sold
            # quantity, keeping the reserved-coins bookkeeping consistent.
            wallet.release_coins(transaction.coin_id, transaction.quantity)
            # Update wallet balance and assets for sell order
            wallet.update_balance_add(
                transaction.quantity * coin_market_prices[transaction.coin_id]
            )
            wallet.update_assets_subtract(transaction.coin_id, transaction.quantity)

        # Update the transaction and the wallet in the database
        db.session.add(transaction)
        db.session.add(wallet)

    while True:
        from app import app

        start = time.monotonic()

        with app.app_context():
            coins = set()
            coin_market_prices = {}

            # Get list of all open trades (market and limit) in the database
            open_transactions = Transaction.query.filter(
                or_(
                    and_(
                        Transaction.orderType == "limit", Transaction.status == "open"
                    ),
                    and_(Transaction.orderType == "stop", Transaction.status == "open"),
                )
            ).all()

            # Get all unique coins involved in open trades
            for transaction in open_transactions:
                coins.add(transaction.coin_id)

            # Get market prices for all coins involved in open trades, iterating through
            # 250 coins at a time to adhere to CoinGecko API rate limits
            coins, coin_market_prices = list(coins), {}

            for i in range(0, len(coins), 250):
                current_batch = coins[i : i + 250]
                current_batch = ",".join(current_batch)

                url = "https://api.coingecko.com/api/v3/coins/markets"
                params = {"vs_currency": "usd", "per_page": 250, "ids": current_batch}
                try:
                    response = requests.get(
                        url,
                        params=params,
                        headers=COINGECKO_API_HEADERS,
                        timeout=10,
                    )
                    response.raise_for_status()
                    data = response.json()
                except (requests.RequestException, ValueError):
                    # Skip this batch on network/HTTP/invalid-JSON errors; coins
                    # left without a price are skipped in the loop below.
                    continue

                # A rate-limit/error body may be a dict rather than the expected list
                if not isinstance(data, list):
                    continue

                for coin in data:
                    coin_market_prices[coin["id"]] = D(coin["current_price"])

            # Update each open trade, seeing if it can be closed. Each trade is
            # processed under a row-level lock on its wallet and committed
            # individually so FOR UPDATE locks are released promptly and never held
            # across the whole batch (which would block live requests).
            for transaction in open_transactions:
                # Skip trades whose coin price could not be fetched this cycle
                if transaction.coin_id not in coin_market_prices:
                    continue

                try:
                    # Lock the wallet for this transaction; check/fill against the
                    # locked instance so balance/holdings reads are authoritative.
                    wallet = _lock_wallet(transaction.wallet_id)
                    if wallet is None:
                        db.session.rollback()
                        continue

                    # Re-check status under the lock: another executor may have
                    # filled or cancelled this order between the initial query and
                    # acquiring the wallet lock. Refresh forces a fresh read so we
                    # see the committed status and skip it (prevents double-execution).
                    db.session.refresh(transaction)
                    if transaction.status != "open":
                        continue

                    if transaction.orderType == "limit":
                        if (
                            transaction.transactionType == "buy"
                            and coin_market_prices[transaction.coin_id]
                            <= transaction.price_per_unit
                        ):
                            # Funds for this order were reserved at placement
                            # (qty x trigger price). Only the overage above that
                            # reservation - qty x (market - trigger), positive only
                            # for a stop buy whose price rose past the trigger - must
                            # come from available (unreserved) balance. Checking total
                            # balance here would let a stop buy cross-spend funds
                            # reserved for the user's other open orders.
                            overage = transaction.quantity * (
                                coin_market_prices[transaction.coin_id]
                                - transaction.price_per_unit
                            )
                            if wallet.has_enough_available_balance(overage):
                                # Execute the order
                                execute_open_order(transaction, True, wallet)
                            else:
                                # Not enough free funds to cover the overage; cancel it
                                cancel_open_order(transaction, wallet)
                        elif (
                            transaction.transactionType == "sell"
                            and coin_market_prices[transaction.coin_id]
                            >= transaction.price_per_unit
                        ):
                            # If the user has enough coins to execute the trade, execute it
                            if wallet.has_enough_coins(
                                transaction.coin_id, transaction.quantity
                            ):
                                # Execute the order
                                execute_open_order(transaction, False, wallet)
                            else:
                                # If user doesn't have enough coins to execute the trade, cancel it
                                cancel_open_order(transaction, wallet)
                    elif transaction.orderType == "stop":
                        if (
                            transaction.transactionType == "buy"
                            and coin_market_prices[transaction.coin_id]
                            >= transaction.price_per_unit
                        ):
                            # Funds for this order were reserved at placement
                            # (qty x trigger price). Only the overage above that
                            # reservation - qty x (market - trigger), positive only
                            # for a stop buy whose price rose past the trigger - must
                            # come from available (unreserved) balance. Checking total
                            # balance here would let a stop buy cross-spend funds
                            # reserved for the user's other open orders.
                            overage = transaction.quantity * (
                                coin_market_prices[transaction.coin_id]
                                - transaction.price_per_unit
                            )
                            if wallet.has_enough_available_balance(overage):
                                # Execute the order
                                execute_open_order(transaction, True, wallet)
                            else:
                                # Not enough free funds to cover the overage; cancel it
                                cancel_open_order(transaction, wallet)
                        elif (
                            transaction.transactionType == "sell"
                            and coin_market_prices[transaction.coin_id]
                            <= transaction.price_per_unit
                        ):
                            # If the user has enough coins to execute the trade, execute it
                            if wallet.has_enough_coins(
                                transaction.coin_id, transaction.quantity
                            ):
                                # Execute the order
                                execute_open_order(transaction, False, wallet)
                            else:
                                # If user doesn't have enough coins to execute the trade, cancel it
                                cancel_open_order(transaction, wallet)

                    # Commit this trade (and release its wallet lock) before
                    # moving on to the next one.
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    continue

        elapsed = time.monotonic() - start
        time.sleep(max(0, OPEN_TRADE_UPDATE_INTERVAL_SECONDS - elapsed))


def get_coins_data(coin_ids: str, precision: int | None = None):
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {"vs_currency": "usd", "ids": coin_ids}
        if precision:
            params = {**params, "precision": precision}

        response = requests.get(
            url, params=params, headers=COINGECKO_API_HEADERS, timeout=10
        )
        data = response.json()

    except Exception as e:
        raise e

    return data


@core.route("/get_wallet_assets", methods=["GET"])
def get_wallet_assets():
    """
    Retrieves and returns the assets and balance of the currently logged-in user's
    wallet.

    This function accesses the assets attribute of the current user's wallet to obtain
    a dictionary of all assets owned by the user. It also retrieves the current balance
    of the user's wallet. The response is structured as a JSON object that includes
    both the assets dictionary and the balance.

    Returns:
        A JSON response containing a success message along with the user's assets and wallet balance.
    """
    try:
        # Fetch user id
        user_id = get_jwt_identity()
        user = User.query.filter_by(id=user_id).first()

        # Fetch amount of each coin in the user's wallet
        current_assets = user.wallet.assets
        owned_coins = ",".join(current_assets.keys())

        # Fetch current market data for each coin
        coin_market_data = get_coins_data(owned_coins, 2)

        # Filter the coin market data
        coin_market_data = [
            {
                "id": c["id"],
                "name": c["name"],
                "image": c["image"],
                "current_price": c["current_price"],
                "priceChange24h": c["price_change_percentage_24h"],
                "ticker": c["symbol"],
            }
            for c in coin_market_data
        ]

        # Convert coin market data into one big object
        coin_market_data = {c["id"]: c for c in coin_market_data}

        # Merge market data and wallet assets data
        data = [
            {
                "amount": D(val),
                **coin_market_data[c],
                "totalValue": D(val) * D(coin_market_data[c]["current_price"]),
            }
            for c, val in current_assets.items()
        ]
        data.append(
            {
                "totalValue": user.wallet.balance,
                "id": "playusd",
                "name": "PlayUSD",
                "ticker": "USD",
            }
        )

        # Sort by totalValue
        data.sort(key=lambda x: -x["totalValue"])

    except Exception:
        logging.exception("Failed to retrieve wallet assets")
        return jsonify({"error": "Internal server error"}), 500

    return data, 200


@core.route("/get_open_trades", methods=["GET"])
def get_open_trades():
    """
    Retrieves and returns a list of open trade transactions (i.e., limit or stop orders
    that are active/open) for the currently logged-in user.

    This endpoint filters transactions by the current user's wallet ID and 'open'
    status to fetch all open trades associated with the user. Each transaction is
    represented as a dictionary containing key details such as transaction ID, coin ID,
    quantity, price per unit, transaction type, and order type.

    Returns:
        A JSON response containing a success message and the data list of open
        transactions. If an error occurs (e.g., the user has no transactions or the
        database query fails), the function needs proper error handling to manage such
        exceptions.
    """
    try:
        user_id = get_jwt_identity()
        user = User.query.filter_by(id=user_id).first()

        open_transactions = Transaction.query.filter_by(
            wallet_id=user.wallet.id, status="open"
        ).all()

        res = []
        coins = set()

        for transaction in open_transactions:

            temp = {}
            temp["id"] = transaction.id
            temp["coin_id"] = transaction.coin_id
            temp["quantity"] = transaction.quantity
            temp["price_per_unit"] = transaction.price_per_unit
            temp["transaction_type"] = transaction.transactionType
            temp["order_type"] = transaction.orderType
            res.append(temp)
            coins.add(transaction.coin_id)

        coins_data = get_coins_data(",".join(coins), 2)
        coins_data = {
            coin["id"]: [
                coin["current_price"],
                coin["image"],
                coin["symbol"],
                coin["name"],
            ]
            for coin in coins_data
        }

        for coin in res:
            coin["current_price"] = coins_data[coin["coin_id"]][0]
            coin["image"] = coins_data[coin["coin_id"]][1]
            coin["ticker"] = coins_data[coin["coin_id"]][2]
            coin["name"] = coins_data[coin["coin_id"]][3]

        return jsonify(res), 200
    except Exception:
        logging.exception("get_open_trades failed")
        return jsonify({"error": "Internal server error"}), 502


@core.route("/cancel_open_trade", methods=["POST"])
def cancel_open_trade():
    """
    Cancels an open trade transaction (i.e., a limit or stop order that is currently
    still active/open).

    This function handles a POST request to cancel an open transaction. It retrieves
    the transaction ID from the JSON payload of the request, finds the corresponding
    transaction in the database, and invokes the cancel_open_order method on the
    transaction object. After updating the transaction status, it commits the changes
    to the database.

    Returns:
        A JSON response indicating the success of the operation and HTTP status code 200.
    """
    try:
        data = request.get_json(silent=True) or {}
        transaction_id = data.get("transaction_id")
        if not transaction_id:
            return jsonify({"error": "Missing transaction_id"}), 400

        # Get the transaction object, locking the row to prevent a concurrent
        # background executor from filling it while we cancel it
        transaction = db.session.scalar(
            db.select(Transaction).filter_by(id=transaction_id).with_for_update()
        )
        if not transaction:
            return jsonify({"error": "Order no longer exists"}), 404

        # Get the transaction owner's wallet id
        user_id = get_jwt_identity()
        wallet_id = Wallet.query.filter_by(owner_id=user_id).first().id

        # Verify that the transaction belongs to the user sending the request
        if transaction.wallet_id != wallet_id:
            return (
                jsonify(
                    {"error": "Transaction does not belong to the requesting user"}
                ),
                403,
            )

        # Reject if the order is no longer open (e.g. already filled/cancelled)
        if transaction.status != "open":
            return (
                jsonify({"error": "Order is no longer open and cannot be cancelled"}),
                409,
            )

        # Row-lock the wallet so the reservation release happens atomically with the
        # status flip (and never races the background executor's own wallet lock).
        wallet = _lock_wallet(transaction.wallet_id)
        if wallet is None:
            db.session.rollback()
            return jsonify({"error": "Wallet not found"}), 404

        # Release the funds/coins reserved at placement (computed at trigger price)
        if transaction.transactionType == "buy":
            wallet.release_balance(transaction.quantity * transaction.price_per_unit)
        else:
            wallet.release_coins(transaction.coin_id, transaction.quantity)

        # Else cancel the order and commit to the db
        transaction.cancel_open_order()
        db.session.add(transaction)
        db.session.add(wallet)
        db.session.commit()

        return jsonify({"success": "Transaction successfully cancelled"}), 200
    except Exception:
        return jsonify({"error": "Transaction could not be cancelled"}), 500


@core.route("/get_top_coins", methods=["POST"])
def get_top_coins():
    """
    Retrieve and return a list of the top coins from the CoinGecko API, sorted by a
    user-specified criterion.

    The function fetches JSON data from the CoinGecko API based on the sorting
    parameter received from the client. The response includes various details about the
    coins such as current price, price change percentages over different time frames,
    and sparkline data.

    The endpoint accepts a POST request with a JSON body that specifies the sorting
    criteria.

    Returns:
        A JSON response containing an array of the top 100 cryptocurrencies sorted
        according to the specified parameter. Each item in the array includes detailed
        market data of the coin.
    """
    try:
        data = request.get_json()
        sort_coins_by = data["sort_coins_by"]

        # Validate the sort_coins_by argument
        if sort_coins_by not in {
            "market_cap_asc",
            "market_cap_desc",
            "volume_asc",
            "volume_desc",
        }:
            return (
                jsonify(
                    {
                        "error": "Invalid sort_coins_by value. Must be one of: market_cap_asc, market_cap_desc, volume_asc, volume_desc."
                    }
                ),
                400,
            )

        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": sort_coins_by,
            "per_page": 100,
            "page": 1,
            "price_change_percentage": "1h,24h,7d",
            "precision": 2,
            "sparkline": "true",
        }

        response = requests.get(
            url, params=params, headers=COINGECKO_API_HEADERS, timeout=10
        )
        data = response.json()
        temp = []
        for coin in data:
            temp.append(
                {
                    **coin,
                    "identity": {"name": coin["name"], "symbol": coin["symbol"]},
                    "sparkline_in_7d": coin["sparkline_in_7d"]["price"],
                }
            )
        return jsonify(temp)
    except Exception:
        logging.exception("get_top_coins failed")
        return jsonify({"error": "Internal server error"}), 502


@core.route("/get_coin_data/<coin_id>", methods=["GET"])
def get_coin_data(coin_id: str):
    """
    Fetches and returns detailed market data for a specific coin.

    This function processes a POST request that includes JSON data with a 'coin_id'
    key. It constructs a query to the CoinGecko API to retrieve current market data for
    the specified coin in USD, including the price change percentage over the last 24
    hours.

    Returns:
        Flask.Response: A JSON response containing detailed market data for the specified cryptocurrency coin.
    """
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "ids": coin_id,
            "price_change_percentage": "24h",
            "precision": 5,
        }

        response = requests.get(
            url, params=params, headers=COINGECKO_API_HEADERS, timeout=10
        )
        data = response.json()
        data = jsonify(data[0])

        return data
    except Exception:
        logging.exception("get_coin_data failed")
        return jsonify({"error": "Internal server error"}), 502


@core.route("/get_coin_sparkline/<coin_id>", methods=["GET"])
def get_coin_sparkline(coin_id: str):
    try:
        get_coins_list_cached()
        if coin_id not in _COINS_LIST_CACHE_SET:
            return jsonify({"error": f"Unknown coin id: {coin_id}"}), 404

        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
        params = {
            "vs_currency": "usd",
            "days": "7",
            "interval": "hourly",
        }

        response = requests.get(
            url, params=params, headers=COINGECKO_API_HEADERS, timeout=10
        )
        data = response.json()
        data = data["prices"]
        data = [price for tick, price in data]
        data = jsonify(data)

        return data
    except Exception:
        return jsonify({"error": "Internal server error"}), 502


@core.route("/get_user_balance", methods=["GET"])
def get_user_balance():
    user_id = get_jwt_identity()
    user = User.query.filter_by(id=user_id).first()
    user_balance = user.wallet.balance
    data = jsonify(user_balance)
    return data, 200


@core.route("/get_coin_balance/<coin_id>", methods=["GET"])
def get_coin_balance(coin_id: str):
    """
    Fetches and returns the balance of a specific coin from the current user's wallet.

    This function handles a POST request with JSON content including a 'coin_id'. It
    retrieves the balance of the specified coin from the current user's wallet. If the
    coin is not found in the wallet, it returns a balance of 0.

    Returns:
        Flask.Response: A JSON response containing the balance of the specified coin in
                        the user's wallet.

    """
    user_id = get_jwt_identity()
    user = User.query.filter_by(id=user_id).first()

    # Get coin balance from curent user's wallet corresponding with the coin_id
    coin_balance = qty_get(user.wallet.assets, coin_id)
    data = jsonify(coin_balance)

    return data, 200


@core.route("/get_all_coin_names")
def get_all_coin_names():
    """
    Fetch and return a list of all cryptocurrency coins available on CoinGecko.

    This function processes a GET request and queries the CoinGecko API at the
    '/coins/list' endpoint, which provides a comprehensive list of all cryptocurrencies
    tracked by CoinGecko, including their IDs, symbols, and names.

    Returns:
        Flask.Response: A JSON response containing a list of all cryptocurrencies, with each entry including
        the coin's ID, symbol, and name.
    """
    try:
        return jsonify(get_coins_list_cached())
    except Exception:
        return jsonify({"error": "Internal server error"}), 502


@core.route("/get_trending_coins")
def get_trending_coins():
    """
    Fetch and return data for currently trending coins from the CoinGecko API.

    This function handles a GET request and queries the CoinGecko API's trending
    endpoint, which provides data on the most popular cryptocurrencies based on recent
    search activities.

    Returns:
        Flask.Response: A JSON response containing data about trending cryptocurrency coins.
    """
    url = "https://api.coingecko.com/api/v3/search/trending"

    try:
        response = requests.get(url, headers=COINGECKO_API_HEADERS, timeout=10)

        data = response.json()
        data = data["coins"]
        data = [coin["item"] for coin in data]

        data = [
            {
                "coin_id": coin["id"],
                "name": coin["name"],
                "thumb": coin["thumb"],
                "symbol": coin["symbol"],
                "market_cap_rank": coin["market_cap_rank"],
                "price": coin["data"]["price"],
                "total_volume": int(coin["data"]["total_volume"][1:].replace(",", "")),
                "market_cap": int(coin["data"]["market_cap"][1:].replace(",", "")),
                "price_change_percentage_24h": {
                    "usd": coin["data"]["price_change_percentage_24h"]["usd"],
                    "btc": coin["data"]["price_change_percentage_24h"]["btc"],
                },
            }
            for coin in data
        ]

        return jsonify(data)
    except Exception:
        logging.exception("Failed to fetch top coins data")
        return jsonify({"error": "Internal server error"}), 500


@core.route("/get_coin_OHLC_data/<coin_id>", methods=["GET"])
def get_coin_OHLC_data(coin_id: str):
    """
    Fetch and return the Open, High, Low, and Close (OHLC) market data for a specified
    coin over the past year.

    This function processes a POST request containing JSON data with a 'coin_id' key.
    It uses this ID to query the CoinGecko API and retrieves OHLC data in USD for the
    specified coin over the last 365 days.

    Returns:
        Flask.Response: A JSON response containing the OHLC data for the specified coin.
    """
    try:
        get_coins_list_cached()
        if coin_id not in _COINS_LIST_CACHE_SET:
            return jsonify({"error": f"Unknown coin id: {coin_id}"}), 404

        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc?vs_currency=usd&days=365"

        response = requests.get(url, headers=COINGECKO_API_HEADERS, timeout=10)
        data = response.json()
        data = jsonify(data)

        return data
    except Exception:
        return jsonify({"error": "Internal server error"}), 502


@core.route("/get_coin_historical_data/<coin_id>", methods=["GET"])
def get_coin_historical_data(coin_id: str):
    """
    Fetch and return historical market data for a specified coin over the past year.

    This function handles a POST request with JSON content that includes a 'coin_id'.
    It queries the CoinGecko API to retrieve daily market chart data in USD for the
    specified coin over the last 365 days.

    Returns:
        Flask.Response: A JSON response containing the historical market data.
    """
    try:
        get_coins_list_cached()
        if coin_id not in _COINS_LIST_CACHE_SET:
            return jsonify({"error": f"Unknown coin id: {coin_id}"}), 404

        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?vs_currency=usd&days=365&interval=daily"

        response = requests.get(url, headers=COINGECKO_API_HEADERS, timeout=10)
        data = response.json()
        data = jsonify(data)

        return data
    except Exception:
        return jsonify({"error": "Internal server error"}), 502
