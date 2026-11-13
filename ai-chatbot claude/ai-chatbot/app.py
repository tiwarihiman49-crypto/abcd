"""Flask web application for the Gemini chatbot."""

import hmac
import io
import os
import re
import secrets
import threading
import time
from collections import OrderedDict, defaultdict, deque
from contextlib import redirect_stdout

from flask import Flask, jsonify, make_response, render_template, request, send_from_directory, session
from werkzeug.exceptions import RequestEntityTooLarge
from google.genai import errors

import analytics
import chat_logger
import config
from gemini_client import GeminiChatbot

app = Flask(__name__)

if (
    not config.SECRET_KEY
    or len(config.SECRET_KEY) < 32
    or config.SECRET_KEY.startswith("replace_with_")
):
    raise RuntimeError(
        "Set SECRET_KEY to a random value of at least 32 characters."
    )

app.config.update(
    SECRET_KEY=config.SECRET_KEY,
    MAX_CONTENT_LENGTH=16 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(
        os.environ.get("SESSION_COOKIE_SECURE", "").lower() == "true"
        or os.environ.get("RENDER", "").lower() == "true"
    ),
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
)

CHART_FILES = (
    "1_response_time.png",
    "2_question_vs_answer.png",
    "3_time_per_chat.png",
)
MAX_ACTIVE_BOTS = 256
MAX_RATE_LIMIT_BUCKETS = 4096
RATE_LIMIT_WINDOW = 60
DEFAULT_API_LIMIT = 60
ASK_API_LIMIT = 10
GLOBAL_ASK_API_LIMIT = 30
MODEL_API_LIMIT = 15

_bots = OrderedDict()
_bot_lock = threading.Lock()
_analytics_lock = threading.Lock()
_rate_limit_lock = threading.Lock()
_rate_limits = defaultdict(deque)


def _workspace_id():
    """Return the opaque workspace ID held in the signed browser session."""
    workspace_id = session.get("workspace_id")
    if not isinstance(workspace_id, str) or not re.fullmatch(r"[0-9a-f]{32}", workspace_id):
        workspace_id = secrets.token_hex(16)
        session["workspace_id"] = workspace_id
    session.permanent = True
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return workspace_id


def _workspace_charts_dir(workspace_id):
    if not re.fullmatch(r"[0-9a-f]{32}", workspace_id):
        raise ValueError("Invalid workspace identifier.")
    return os.path.join(config.CHARTS_DIR, workspace_id)


def _get_bot(workspace_id):
    if not config.GEMINI_API_KEY:
        return None
    with _bot_lock:
        bot = _bots.get(workspace_id)
        if bot is None:
            try:
                bot = GeminiChatbot()
            except Exception as error:
                app.logger.warning("Gemini client initialization failed (%s)", type(error).__name__)
                return None
            _bots[workspace_id] = bot
        _bots.move_to_end(workspace_id)
        while len(_bots) > MAX_ACTIVE_BOTS:
            _bots.popitem(last=False)
        return bot


def error_response(message, status=500):
    return jsonify({"ok": False, "error": message}), status


@app.before_request
def protect_api():
    if not request.path.startswith("/api/"):
        return None

    workspace_id = _workspace_id()

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        submitted_token = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(submitted_token, session["csrf_token"]):
            return error_response("Request verification failed. Refresh the page and try again.", 403)

    if request.method in {"POST", "PUT", "PATCH"} and not request.is_json:
        return error_response("A JSON request body is required.", 415)

    limit = DEFAULT_API_LIMIT
    if request.endpoint == "ask":
        limit = ASK_API_LIMIT
    elif request.endpoint == "models":
        limit = MODEL_API_LIMIT

    now = time.monotonic()
    endpoint = request.endpoint or request.path
    bucket_limits = [((workspace_id, endpoint), limit)]
    if endpoint == "ask":
        bucket_limits.append((("__global__", "ask"), GLOBAL_ASK_API_LIMIT))

    with _rate_limit_lock:
        new_keys = sum(key not in _rate_limits for key, _ in bucket_limits)
        if len(_rate_limits) + new_keys > MAX_RATE_LIMIT_BUCKETS:
            for old_key, old_bucket in list(_rate_limits.items()):
                while old_bucket and now - old_bucket[0] >= RATE_LIMIT_WINDOW:
                    old_bucket.popleft()
                if not old_bucket:
                    del _rate_limits[old_key]
            new_keys = sum(key not in _rate_limits for key, _ in bucket_limits)
            if len(_rate_limits) + new_keys > MAX_RATE_LIMIT_BUCKETS:
                response = make_response(
                    jsonify({"ok": False, "error": "The service is busy. Please try again shortly."}),
                    503,
                )
                response.headers["Retry-After"] = str(RATE_LIMIT_WINDOW)
                return response
        for key, _ in bucket_limits:
            bucket = _rate_limits.get(key)
            if bucket:
                while bucket and now - bucket[0] >= RATE_LIMIT_WINDOW:
                    bucket.popleft()
        for key, request_limit in bucket_limits:
            bucket = _rate_limits.get(key, ())
            if len(bucket) >= request_limit:
                response = make_response(
                    jsonify({"ok": False, "error": "Too many requests. Please wait and try again."}),
                    429,
                )
                response.headers["Retry-After"] = str(RATE_LIMIT_WINDOW)
                return response
        for key, _ in bucket_limits:
            _rate_limits.setdefault(key, deque()).append(now)
    return None


@app.after_request
def add_security_headers(response):
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'",
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.path == "/" or request.path.startswith(("/api/", "/charts/")):
        response.headers["Cache-Control"] = "no-store"
    if os.environ.get("RENDER", "").lower() == "true":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.errorhandler(RequestEntityTooLarge)
def request_too_large(error):
    return error_response("Request body is too large.", error.code or 413)


@app.route("/")
def index():
    _workspace_id()
    return render_template("index.html", csrf_token=session["csrf_token"])


@app.route("/health")
def health():
    return jsonify({"ok": True})


@app.route("/api/ask", methods=["POST"])
def ask():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("A JSON object is required.", 400)
    question = data.get("question")
    if not isinstance(question, str):
        return error_response("Question must be text.", 400)
    question = question.strip()
    if not question:
        return error_response("Please type a question first.", 400)
    if len(question) > 4000:
        return error_response("Question must be 4,000 characters or fewer.", 400)

    workspace_id = _workspace_id()
    bot = _get_bot(workspace_id)
    if bot is None:
        return error_response(
            "The chatbot is not configured or is temporarily unavailable. Please try again later.",
            503,
        )

    try:
        with _bot_lock:
            answer, seconds = bot.ask(question)
            chat_logger.save_chat(question, answer, seconds, workspace_id)
    except errors.APIError as error:
        app.logger.warning("Gemini request failed (%s)", type(error).__name__)
        return error_response("Gemini could not complete the request. Please try again.", 502)
    except Exception as error:
        app.logger.warning("Chat request failed (%s)", type(error).__name__)
        return error_response("The request could not be completed. Please try again.", 500)

    return jsonify({"ok": True, "answer": answer, "seconds": seconds})


@app.route("/api/reset", methods=["POST"])
def reset():
    workspace_id = _workspace_id()
    bot = _get_bot(workspace_id)
    if bot is None:
        return error_response("The chatbot is not configured or is temporarily unavailable.", 503)
    try:
        with _bot_lock:
            bot.reset()
    except Exception as error:
        app.logger.warning("Conversation reset failed (%s)", type(error).__name__)
        return error_response("The conversation could not be reset. Please try again.", 500)
    return jsonify({"ok": True})


@app.route("/api/models")
def models():
    bot = _get_bot(_workspace_id())
    if bot is None:
        return error_response("The chatbot is not configured or is temporarily unavailable.", 503)
    try:
        with _bot_lock:
            names = bot.list_models()
    except Exception as error:
        app.logger.warning("Model listing failed (%s)", type(error).__name__)
        return error_response("Models could not be loaded. Please try again later.", 502)
    return jsonify({"ok": True, "models": names})


@app.route("/api/history")
def history():
    try:
        df = chat_logger.load_history(_workspace_id())
        rows = df[["chat_no", "question", "response_time_sec"]].tail(5)
    except Exception as error:
        app.logger.warning("Chat history could not be loaded (%s)", type(error).__name__)
        return error_response("Chat history could not be loaded.", 500)
    return jsonify({"ok": True, "rows": rows.to_dict(orient="records")})


@app.route("/api/stats")
def stats():
    try:
        df = chat_logger.load_history(_workspace_id())
        output = io.StringIO()
        with _analytics_lock, redirect_stdout(output):
            analytics.show_summary(df)
    except Exception as error:
        app.logger.warning("Chat statistics could not be generated (%s)", type(error).__name__)
        return error_response("Statistics could not be generated.", 500)
    return jsonify({"ok": True, "text": output.getvalue().strip()})


@app.route("/api/charts", methods=["POST"])
def charts():
    workspace_id = _workspace_id()
    try:
        df = chat_logger.load_history(workspace_id)
        output = io.StringIO()
        charts_dir = _workspace_charts_dir(workspace_id)
        with _analytics_lock, redirect_stdout(output):
            analytics.create_charts(df, charts_dir=charts_dir)
    except Exception as error:
        app.logger.warning("Charts could not be generated (%s)", type(error).__name__)
        return error_response("Charts could not be generated.", 500)

    files = []
    if len(df) >= 2:
        files = [
            filename
            for filename in CHART_FILES
            if os.path.isfile(os.path.join(charts_dir, filename))
        ]
    message = "Charts are ready." if files else output.getvalue().strip()
    return jsonify({"ok": True, "message": message, "files": files})


@app.route("/charts/<path:filename>")
def chart_image(filename):
    if filename not in CHART_FILES:
        return error_response("Chart not found.", 404)
    charts_dir = _workspace_charts_dir(_workspace_id())
    return send_from_directory(charts_dir, filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
