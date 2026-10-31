import os
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    jsonify,
    has_request_context
)

from werkzeug.middleware.proxy_fix import ProxyFix
from flask_sqlalchemy import SQLAlchemy
from ai_service import generate_post


# =====================================================
# LOAD ENVIRONMENT VARIABLES
# =====================================================

load_dotenv()


# =====================================================
# PROJECT LOCATION
# =====================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

TEMPLATES_DIR = os.path.join(
    BASE_DIR,
    "templates"
)

STATIC_DIR = os.path.join(
    BASE_DIR,
    "static"
)

UPLOAD_FOLDER = os.path.join(
    STATIC_DIR,
    "uploads"
)

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png"}


# =====================================================
# FLASK APP SETUP & PROXY FIX (Render Reverse Proxy)
# =====================================================

app = Flask(
    __name__,
    template_folder=TEMPLATES_DIR,
    static_folder=STATIC_DIR
)

# Render & cloud providers use reverse proxies terminating SSL
# ProxyFix ensures request.scheme is 'https' and request.host uses X-Forwarded-Host
app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=1,
    x_proto=1,
    x_host=1,
    x_prefix=1
)

# 5 MB maximum file upload size
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


# =====================================================
# DATABASE CONFIGURATION
# =====================================================

DATABASE_PATH = os.path.join(
    BASE_DIR,
    "database.db"
)

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URI",
    "sqlite:///" + DATABASE_PATH
)

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# =====================================================
# LINKEDIN CONFIGURATION & DYNAMIC REDIRECT URI
# =====================================================

LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
LINKEDIN_POST_URL = "https://api.linkedin.com/rest/posts"

LINKEDIN_VERSION = os.getenv(
    "LINKEDIN_VERSION",
    "202607"
)


def get_linkedin_redirect_uri() -> str:
    """
    Dynamically computes the exact LinkedIn OAuth callback URL.
    Prevents 'Tunnel Unavailable' and localhost callback errors in production by:
    1. Detecting if the current request is running on a live remote host (e.g. *.onrender.com)
       and bypassing any stale localhost or dead tunnel URLs saved in .env.
    2. Using RENDER_EXTERNAL_URL if set in the Render environment.
    3. Respecting explicit production LINKEDIN_REDIRECT_URI if configured.
    4. Dynamically extracting scheme and host from request headers (X-Forwarded-Proto, X-Forwarded-Host).
    5. Falling back to localhost only for local development.
    """
    configured_uri = (os.getenv("LINKEDIN_REDIRECT_URI") or "").strip()

    is_configured_local_or_tunnel = any(
        t in configured_uri.lower()
        for t in ("localhost", "127.0.0.1", "localtunnel.me", "ngrok", "trycloudflare")
    )

    # 1. Check Render's built-in external URL variable
    render_external_url = (os.getenv("RENDER_EXTERNAL_URL") or "").strip()
    if render_external_url:
        target = render_external_url.rstrip("/")
        if not target.endswith("/auth/linkedin/callback"):
            target += "/auth/linkedin/callback"
        # If configured_uri was empty or pointed to localhost/tunnel, use Render URL
        if not configured_uri or is_configured_local_or_tunnel:
            return target

    # 2. Check general base URL variables
    for env_var in ("BASE_URL", "APP_URL", "SERVER_URL"):
        val = (os.getenv(env_var) or "").strip()
        if val and not any(t in val.lower() for t in ("localhost", "127.0.0.1")):
            target = val.rstrip("/")
            if not target.endswith("/auth/linkedin/callback"):
                target += "/auth/linkedin/callback"
            if not configured_uri or is_configured_local_or_tunnel:
                return target

    # 3. Dynamic inspection from active Flask request headers
    if has_request_context():
        try:
            req_host = (request.headers.get("X-Forwarded-Host") or request.host or "").strip()
            req_proto = (request.headers.get("X-Forwarded-Proto") or request.scheme or "").strip()

            is_req_local = any(t in req_host.lower() for t in ("localhost", "127.0.0.1"))

            # If request is from a public/production host (like onrender.com)
            if req_host and not is_req_local:
                # Force HTTPS for non-local production hosts
                proto = "https" if ("render.com" in req_host or not req_proto or req_proto == "http") else req_proto
                return f"{proto}://{req_host}/auth/linkedin/callback"

            # If running locally and user defined configured_uri
            if is_req_local and configured_uri:
                if not configured_uri.endswith("/auth/linkedin/callback"):
                    configured_uri = configured_uri.rstrip("/") + "/auth/linkedin/callback"
                return configured_uri

            if is_req_local and req_host:
                proto = req_proto or "http"
                return f"{proto}://{req_host}/auth/linkedin/callback"
        except Exception as e:
            print(f"⚠️ Notice resolving dynamic redirect uri: {e}")

    # 4. If explicit non-local configured URI was set in environment
    if configured_uri and not is_configured_local_or_tunnel:
        if not configured_uri.endswith("/auth/linkedin/callback"):
            configured_uri = configured_uri.rstrip("/") + "/auth/linkedin/callback"
        return configured_uri

    # 5. Localhost fallback
    if configured_uri:
        if not configured_uri.endswith("/auth/linkedin/callback"):
            configured_uri = configured_uri.rstrip("/") + "/auth/linkedin/callback"
        return configured_uri

    return "http://localhost:5000/auth/linkedin/callback"


# =====================================================
# POST DATABASE MODEL
# =====================================================

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    scheduled_at = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default="scheduled")
    error_message = db.Column(db.Text, nullable=True)
    published_at = db.Column(db.DateTime, nullable=True)

    # Media support fields
    image_path = db.Column(db.String(255), nullable=True)
    media_type = db.Column(db.String(50), nullable=True)
    media_status = db.Column(db.String(50), nullable=True)
    linkedin_media_id = db.Column(db.String(255), nullable=True)
    linkedin_post_id = db.Column(db.String(255), nullable=True)


# =====================================================
# LINKEDIN ACCOUNT MODEL
# =====================================================

class LinkedInAccount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    linkedin_id = db.Column(db.String(255), nullable=False)
    access_token = db.Column(db.Text, nullable=False)
    connected_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )


# =====================================================
# OAUTH STATE MODEL
# =====================================================

class OAuthState(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    state = db.Column(db.String(255), nullable=False, unique=True)
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )


# =====================================================
# IMAGE UPLOAD VALIDATION HELPER
# =====================================================

def validate_and_save_image(file):
    """
    Validates the uploaded image both by extension and file signature (magic bytes),
    enforces a safe generated filename, and saves it into the uploads folder.
    Returns: (relative_image_path, media_type, error_message)
    """
    if not file or file.filename == "":
        return None, None, None

    filename = file.filename
    if "." not in filename:
        return None, None, "File must have an image extension (.jpg, .jpeg, or .png)."

    ext = filename.rsplit(".", 1)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return None, None, "Unsupported file format. Only JPG, JPEG, and PNG images are allowed."

    # Validate file magic bytes (file signature)
    header = file.read(16)
    file.seek(0)

    is_jpeg = header.startswith(b"\xff\xd8\xff")
    is_png = header.startswith(b"\x89PNG\r\n\x1a\n")

    if not (is_jpeg or is_png):
        return None, None, "Invalid image content. File signature does not match a valid JPG or PNG."

    # Generate safe random filename to prevent path traversal and collisions
    safe_filename = f"{secrets.token_hex(16)}.{ext}"
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    save_path = os.path.join(UPLOAD_FOLDER, safe_filename)
    file.save(save_path)

    media_type = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
    relative_path = f"uploads/{safe_filename}"
    return relative_path, media_type, None


# =====================================================
# DASHBOARD ROUTE
# =====================================================

@app.route("/")
def dashboard():
    posts = Post.query.order_by(Post.scheduled_at.desc()).all()
    total = Post.query.count()
    scheduled = Post.query.filter_by(status="scheduled").count()
    published = Post.query.filter_by(status="published").count()
    failed = Post.query.filter_by(status="failed").count()

    media_count = Post.query.filter(Post.image_path.isnot(None)).count()
    text_count = Post.query.filter(Post.image_path.is_(None)).count()

    linkedin_account = LinkedInAccount.query.first()

    return render_template(
        "dashboard.html",
        posts=posts,
        total=total,
        scheduled=scheduled,
        published=published,
        failed=failed,
        media_count=media_count,
        text_count=text_count,
        linkedin_account=linkedin_account
    )


# =====================================================
# SCHEDULE POST
# =====================================================

@app.route("/schedule", methods=["POST"])
def schedule_post():
    content = request.form.get("content", "").strip()
    scheduled_at = request.form.get("scheduled_at", "").strip()
    image_file = request.files.get("image")

    if not content or not scheduled_at:
        return redirect("/")

    try:
        scheduled_datetime = datetime.fromisoformat(scheduled_at)
    except ValueError:
        return '<h2>Invalid date/time</h2><a href="/">Back to dashboard</a>'

    image_path = None
    media_type = None
    media_status = None

    if image_file and image_file.filename.strip():
        image_path, media_type, error = validate_and_save_image(image_file)
        if error:
            return f'<h2>Image Upload Error</h2><p>{error}</p><a href="/">Back to dashboard</a>', 400
        media_status = "attached"

    post = Post(
        content=content,
        scheduled_at=scheduled_datetime,
        status="scheduled",
        image_path=image_path,
        media_type=media_type,
        media_status=media_status
    )

    db.session.add(post)
    db.session.commit()

    return redirect("/")


# =====================================================
# DELETE POST
# =====================================================

@app.route("/delete/<int:post_id>", methods=["POST"])
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)

    # Safely remove uploaded image file if present
    if post.image_path:
        full_path = os.path.join(STATIC_DIR, post.image_path)
        try:
            if os.path.exists(full_path):
                os.remove(full_path)
        except OSError:
            pass

    db.session.delete(post)
    db.session.commit()
    return redirect("/")


# =====================================================
# AI POST GENERATION API
# =====================================================

@app.route("/api/generate-post", methods=["POST"])
def api_generate_post():
    try:
        data = request.get_json(silent=True) or {}
        keywords = data.get("keywords", "").strip()
        tone = data.get("tone", "Professional").strip()

        if not keywords:
            return jsonify({
                "success": False,
                "error": "Please provide keywords or rough ideas to generate a post."
            }), 400

        result = generate_post(keywords=keywords, tone=tone)
        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"AI service error: {str(e)}"
        }), 500


# =====================================================
# LINKEDIN CONNECT (OAUTH START)
# =====================================================

@app.route("/auth/linkedin")
def linkedin_login():
    if not LINKEDIN_CLIENT_ID or not LINKEDIN_CLIENT_SECRET:
        return '<h2>LinkedIn configuration error</h2><p>LINKEDIN_CLIENT_ID or LINKEDIN_CLIENT_SECRET missing in .env file.</p><a href="/">Back</a>'

    # Prune expired states older than 15 minutes to keep DB clean
    try:
        fifteen_mins_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=15)
        OAuthState.query.filter(OAuthState.created_at < fifteen_mins_ago).delete()
        db.session.commit()
    except Exception:
        db.session.rollback()

    state = secrets.token_urlsafe(32)

    oauth_state = OAuthState(state=state)
    db.session.add(oauth_state)
    db.session.commit()

    scopes = "openid profile email w_member_social"
    redirect_uri = get_linkedin_redirect_uri()
    print(f"🔗 [LinkedIn OAuth] Initiating login with redirect_uri: {redirect_uri}", flush=True)

    params = {
        "response_type": "code",
        "client_id": LINKEDIN_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": scopes
    }

    authorization_url = f"{LINKEDIN_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return redirect(authorization_url)


# =====================================================
# LINKEDIN CALLBACK
# =====================================================

@app.route("/auth/linkedin/callback")
def linkedin_callback():
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")
    error_description = request.args.get("error_description")

    if error:
        return f'<h2>LinkedIn authorization failed</h2><p>{error}: {error_description}</p><a href="/">Back to dashboard</a>'

    if not code or not state:
        return '<h2>Authorization failed</h2><p>Missing code or state.</p><a href="/">Back to dashboard</a>'

    oauth_state = OAuthState.query.filter_by(state=state).first()
    if not oauth_state:
        return '<h2>Security Error</h2><p>OAuth state validation failed.</p><a href="/">Back to dashboard</a>'

    # Validate state is not expired (15 minutes)
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    if oauth_state.created_at and (now_utc_naive - oauth_state.created_at).total_seconds() > 900:
        db.session.delete(oauth_state)
        db.session.commit()
        return '<h2>Security Error</h2><p>OAuth session expired. Please connect again.</p><a href="/">Back to dashboard</a>'

    redirect_uri = get_linkedin_redirect_uri()
    print(f"🔗 [LinkedIn OAuth] Token exchange using redirect_uri: {redirect_uri}", flush=True)

    # 1. Exchange authorization code for access token
    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": LINKEDIN_CLIENT_ID,
        "client_secret": LINKEDIN_CLIENT_SECRET,
        "redirect_uri": redirect_uri
    }

    try:
        token_response = requests.post(
            LINKEDIN_TOKEN_URL,
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20
        )
    except requests.RequestException as e:
        return f'<h2>LinkedIn connection failed</h2><p>{str(e)}</p><a href="/">Back to dashboard</a>'

    if token_response.status_code != 200:
        return f'<h2>Token Error ({token_response.status_code})</h2><pre>{token_response.text}</pre><a href="/">Back to dashboard</a>'

    token_json = token_response.json()
    access_token = token_json.get("access_token")

    if not access_token:
        return '<h2>Token Error</h2><p>LinkedIn did not return an access token.</p><a href="/">Back to dashboard</a>'

    # 2. Fetch User Info using OpenID Connect
    profile_headers = {
        "Authorization": f"Bearer {access_token}"
    }

    try:
        profile_response = requests.get(
            LINKEDIN_USERINFO_URL,
            headers=profile_headers,
            timeout=20
        )
    except requests.RequestException as e:
        return f'<h2>Profile Error</h2><p>{str(e)}</p><a href="/">Back to dashboard</a>'

    if profile_response.status_code != 200:
        return f'<h2>Could not identify LinkedIn account</h2><p>HTTP Status: {profile_response.status_code}</p><pre>{profile_response.text}</pre><a href="/">Back to dashboard</a>'

    profile_data = profile_response.json()
    linkedin_id = profile_data.get("sub")

    if not linkedin_id:
        return '<h2>Could not identify LinkedIn account</h2><p>The access token was created, but LinkedIn member information could not be retrieved.</p><a href="/">Back to dashboard</a>'

    # 3. Store or update user in Database
    existing_account = LinkedInAccount.query.first()
    if existing_account:
        existing_account.linkedin_id = linkedin_id
        existing_account.access_token = access_token
        existing_account.connected_at = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        new_account = LinkedInAccount(
            linkedin_id=linkedin_id,
            access_token=access_token,
            connected_at=datetime.now(timezone.utc).replace(tzinfo=None)
        )
        db.session.add(new_account)

    db.session.delete(oauth_state)
    db.session.commit()

    return redirect("/")


# =====================================================
# DISCONNECT LINKEDIN
# =====================================================

@app.route("/auth/linkedin/disconnect", methods=["POST"])
def disconnect_linkedin():
    account = LinkedInAccount.query.first()
    if account:
        db.session.delete(account)
        db.session.commit()
    return redirect("/")


# =====================================================
# TEST LINKEDIN POST
# =====================================================

@app.route("/test-linkedin-post", methods=["POST"])
def test_linkedin_post():
    account = LinkedInAccount.query.first()

    if not account:
        return '<h2>LinkedIn is not connected</h2><a href="/">Back to dashboard</a>'

    post_data = {
        "author": f"urn:li:person:{account.linkedin_id}",
        "commentary": "Hello from my Python LinkedIn Post Scheduler! 🚀",
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": []
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False
    }

    headers = {
        "Authorization": f"Bearer {account.access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "Linkedin-Version": LINKEDIN_VERSION
    }

    try:
        response = requests.post(
            LINKEDIN_POST_URL,
            headers=headers,
            json=post_data,
            timeout=20
        )
    except requests.RequestException as e:
        return f'<h2>Post Request Failed</h2><p>{str(e)}</p><a href="/">Back to dashboard</a>'

    if response.status_code in (200, 201):
        linkedin_post_id = response.headers.get("x-restli-id", "Unknown")
        return f'<h2>Post published successfully! 🎉</h2><pre>ID: {linkedin_post_id}</pre><a href="/">Back to dashboard</a>'

    return f'<h2>LinkedIn Post Failed ({response.status_code})</h2><pre>{response.text}</pre><a href="/">Back to dashboard</a>'


# =====================================================
# CRON / BACKGROUND WORKER TRIGGER ENDPOINT
# =====================================================

@app.route("/cron/publish", methods=["GET", "POST"])
def cron_publish():
    """
    HTTP endpoint for external cron services (cron-job.org, Render cron, etc.)
    or manual testing to execute due post publishing.
    """
    try:
        from scheduler import publish_due_posts
        processed = publish_due_posts(app)
        return jsonify({
            "success": True,
            "processed_posts": processed,
            "timestamp": datetime.now().isoformat()
        }), 200
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# =====================================================
# DATABASE & APSCHEDULER INITIALIZATION (LIFECYCLE)
# =====================================================

def initialize_database():
    """
    Ensures all database tables and required schema migrations are created.
    Runs on both Gunicorn worker boot and direct script execution.
    """
    with app.app_context():
        db.create_all()
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        try:
            with db.engine.connect() as conn:
                cursor = conn.connection.cursor()
                cursor.execute("PRAGMA table_info(post)")
                existing_cols = {row[1] for row in cursor.fetchall()}
                new_columns = [
                    ("image_path", "VARCHAR(255)"),
                    ("media_type", "VARCHAR(50)"),
                    ("media_status", "VARCHAR(50)"),
                    ("linkedin_media_id", "VARCHAR(255)"),
                    ("linkedin_post_id", "VARCHAR(255)")
                ]
                for col_name, col_type in new_columns:
                    if col_name not in existing_cols:
                        cursor.execute(f"ALTER TABLE post ADD COLUMN {col_name} {col_type}")
                conn.connection.commit()
        except Exception as e:
            print(f"Database migration notice: {e}", flush=True)


# Initialize database automatically when module is loaded (Gunicorn / Flask)
initialize_database()

# Initialize APScheduler inside Gunicorn worker process lifecycle
# (runs only once per worker, guarded by lock and reloader checks)
try:
    from scheduler import init_scheduler
    init_scheduler(app, interval_seconds=15)
except Exception as sched_err:
    print(f"⚠️ Notice starting background scheduler: {sched_err}", flush=True)


# =====================================================
# START APPLICATION (LOCAL DEV)
# =====================================================

if __name__ == "__main__":
    app.run(debug=True, port=5000)