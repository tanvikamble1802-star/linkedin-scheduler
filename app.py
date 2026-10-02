import os
import secrets
from datetime import datetime

import requests
from dotenv import load_dotenv

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for
)

from flask_sqlalchemy import SQLAlchemy


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


# =====================================================
# FLASK APP
# =====================================================

app = Flask(
    __name__,
    template_folder=TEMPLATES_DIR,
    static_folder=STATIC_DIR
)


# =====================================================
# DATABASE
# =====================================================

DATABASE_PATH = os.path.join(
    BASE_DIR,
    "database.db"
)

app.config["SQLALCHEMY_DATABASE_URI"] = (
    "sqlite:///" + DATABASE_PATH
)

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# =====================================================
# LINKEDIN CONFIGURATION
# =====================================================

LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")

LINKEDIN_REDIRECT_URI = os.getenv(
    "LINKEDIN_REDIRECT_URI",
    "http://localhost:5000/auth/linkedin/callback"
)

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
LINKEDIN_POST_URL = "https://api.linkedin.com/rest/posts"

LINKEDIN_VERSION = os.getenv(
    "LINKEDIN_VERSION",
    "202607"
)


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


# =====================================================
# LINKEDIN ACCOUNT MODEL
# =====================================================

class LinkedInAccount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    linkedin_id = db.Column(db.String(255), nullable=False)
    access_token = db.Column(db.Text, nullable=False)
    connected_at = db.Column(db.DateTime, default=datetime.utcnow)


# =====================================================
# OAUTH STATE MODEL
# =====================================================

class OAuthState(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    state = db.Column(db.String(255), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# =====================================================
# DASHBOARD
# =====================================================

@app.route("/")
def dashboard():
    posts = Post.query.order_by(Post.scheduled_at.desc()).all()
    total = Post.query.count()
    scheduled = Post.query.filter_by(status="scheduled").count()
    published = Post.query.filter_by(status="published").count()
    failed = Post.query.filter_by(status="failed").count()

    linkedin_account = LinkedInAccount.query.first()

    return render_template(
        "dashboard.html",
        posts=posts,
        total=total,
        scheduled=scheduled,
        published=published,
        failed=failed,
        linkedin_account=linkedin_account
    )


# =====================================================
# SCHEDULE POST
# =====================================================

@app.route("/schedule", methods=["POST"])
def schedule_post():
    content = request.form.get("content", "").strip()
    scheduled_at = request.form.get("scheduled_at", "").strip()

    if not content or not scheduled_at:
        return redirect("/")

    try:
        scheduled_datetime = datetime.fromisoformat(scheduled_at)
    except ValueError:
        return '<h2>Invalid date/time</h2><a href="/">Back to dashboard</a>'

    post = Post(
        content=content,
        scheduled_at=scheduled_datetime,
        status="scheduled"
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
    db.session.delete(post)
    db.session.commit()
    return redirect("/")


# =====================================================
# LINKEDIN CONNECT
# =====================================================

@app.route("/auth/linkedin")
def linkedin_login():
    if not LINKEDIN_CLIENT_ID or not LINKEDIN_CLIENT_SECRET:
        return '<h2>LinkedIn configuration error</h2><p>LINKEDIN_CLIENT_ID or LINKEDIN_CLIENT_SECRET missing in .env file.</p><a href="/">Back</a>'

    state = secrets.token_urlsafe(32)

    oauth_state = OAuthState(state=state)
    db.session.add(oauth_state)
    db.session.commit()

    scopes = "openid profile email w_member_social"

    authorization_url = (
        f"{LINKEDIN_AUTH_URL}"
        f"?response_type=code"
        f"&client_id={LINKEDIN_CLIENT_ID}"
        f"&redirect_uri={LINKEDIN_REDIRECT_URI}"
        f"&state={state}"
        f"&scope={scopes.replace(' ', '%20')}"
    )

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

    # 1. Exchange authorization code for access token
    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": LINKEDIN_CLIENT_ID,
        "client_secret": LINKEDIN_CLIENT_SECRET,
        "redirect_uri": LINKEDIN_REDIRECT_URI
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
        existing_account.connected_at = datetime.utcnow()
    else:
        new_account = LinkedInAccount(
            linkedin_id=linkedin_id,
            access_token=access_token,
            connected_at=datetime.utcnow()
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
# DATABASE INITIALIZATION
# =====================================================

def initialize_database():
    with app.app_context():
        db.create_all()


# =====================================================
# START APPLICATION
# =====================================================

if __name__ == "__main__":
    initialize_database()
    app.run(debug=True)