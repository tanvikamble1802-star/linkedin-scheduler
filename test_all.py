import os
import io
import shutil
import tempfile
import unittest
import urllib.parse
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

# Create dedicated isolated temporary directory for testing database
TEST_TMP_DIR = tempfile.mkdtemp()
TEST_DB_PATH = os.path.join(TEST_TMP_DIR, "test_database.db")

# Force isolated test environment variables BEFORE importing app
os.environ["DATABASE_URI"] = f"sqlite:///{TEST_DB_PATH}"
os.environ["LINKEDIN_CLIENT_ID"] = "test_client_id"
os.environ["LINKEDIN_CLIENT_SECRET"] = "test_client_secret"
os.environ["LINKEDIN_VERSION"] = "202607"

from app import app, db, Post, LinkedInAccount, OAuthState, STATIC_DIR, get_linkedin_redirect_uri
from scheduler import publish_due_posts
import ai_service


class LinkedInSchedulerTestSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False

    @classmethod
    def tearDownClass(cls):
        # Remove temporary test directory and database
        try:
            shutil.rmtree(TEST_TMP_DIR, ignore_errors=True)
        except Exception:
            pass

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            db.create_all()
            # Clear any records in test DB
            Post.query.delete()
            LinkedInAccount.query.delete()
            OAuthState.query.delete()

            # Create dummy connected LinkedIn account
            account = LinkedInAccount(
                linkedin_id="test_member_123",
                access_token="test_token_abc123"
            )
            db.session.add(account)
            db.session.commit()

        # Valid dummy JPEG bytes (magic bytes \xff\xd8\xff)
        self.valid_jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00" + b"\x00" * 100
        # Valid dummy PNG bytes (magic bytes \x89PNG\r\n\x1a\n)
        self.valid_png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 100

    def tearDown(self):
        with app.app_context():
            db.session.rollback()
            Post.query.delete()
            LinkedInAccount.query.delete()
            OAuthState.query.delete()
            db.session.commit()

    # -------------------------------------------------------------
    # TEST 1: Manual text-only post
    # -> schedule -> scheduler processes it -> LinkedIn publishing workflow works
    # -------------------------------------------------------------
    @patch("requests.post")
    def test_1_manual_text_only_post(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.text = '{"id": "urn:li:share:12345"}'
        mock_resp.headers = {"x-restli-id": "urn:li:share:12345"}
        mock_post.return_value = mock_resp

        past_time = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
        res = self.client.post("/schedule", data={
            "content": "Excited to share my journey in software engineering!",
            "scheduled_at": past_time
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        with app.app_context():
            post = Post.query.filter_by(content="Excited to share my journey in software engineering!").first()
            self.assertIsNotNone(post)
            self.assertEqual(post.status, "scheduled")
            self.assertIsNone(post.image_path)

        # Trigger scheduler
        publish_due_posts()

        with app.app_context():
            post = Post.query.filter_by(content="Excited to share my journey in software engineering!").first()
            self.assertEqual(post.status, "published")
            self.assertIsNotNone(post.published_at)
            self.assertIsNone(post.error_message)

    # -------------------------------------------------------------
    # TEST 2: Text + image post
    # -> schedule -> image is handled correctly -> LinkedIn media publishing workflow is triggered
    # -------------------------------------------------------------
    @patch("linkedin.requests.post")
    @patch("linkedin.requests.put")
    def test_2_text_plus_image_post(self, mock_put, mock_post):
        # Step 1: Mock initialize upload response
        mock_init_resp = MagicMock()
        mock_init_resp.status_code = 200
        mock_init_resp.json.return_value = {
            "value": {
                "uploadUrl": "https://media.licdn.com/upload-target",
                "image": "urn:li:image:D5600X987"
            }
        }

        # Step 3: Mock post creation response
        mock_publish_resp = MagicMock()
        mock_publish_resp.status_code = 201
        mock_publish_resp.text = '{"id": "urn:li:share:777"}'

        mock_post.side_effect = [mock_init_resp, mock_publish_resp]

        # Step 2: Mock binary PUT response
        mock_put_resp = MagicMock()
        mock_put_resp.status_code = 201
        mock_put.return_value = mock_put_resp

        past_time = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
        res = self.client.post("/schedule", data={
            "content": "Here is an update with a project screenshot!",
            "scheduled_at": past_time,
            "image": (io.BytesIO(self.valid_jpeg_bytes), "screenshot.jpg")
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        with app.app_context():
            post = Post.query.filter_by(content="Here is an update with a project screenshot!").first()
            self.assertIsNotNone(post)
            self.assertEqual(post.status, "scheduled")
            self.assertIsNotNone(post.image_path)
            self.assertEqual(post.media_status, "attached")
            self.assertEqual(post.media_type, "image/jpeg")

            # Check that physical image file exists on disk
            img_full_path = os.path.join(STATIC_DIR, post.image_path)
            self.assertTrue(os.path.exists(img_full_path))

        # Run scheduler
        publish_due_posts()

        with app.app_context():
            post = Post.query.filter_by(content="Here is an update with a project screenshot!").first()
            self.assertEqual(post.status, "published")
            self.assertEqual(post.media_status, "published")
            self.assertEqual(post.linkedin_media_id, "urn:li:image:D5600X987")
            self.assertIsNone(post.error_message)

        # Verify binary PUT upload was called
        mock_put.assert_called_once()
        self.assertEqual(mock_put.call_args[0][0], "https://media.licdn.com/upload-target")

    # -------------------------------------------------------------
    # TEST 3: AI keyword generation
    # Input: "data analytics, internship, learning, project" -> coherent draft appears
    # -------------------------------------------------------------
    @patch("ai_service.generate_with_gemini")
    def test_3_ai_keyword_generation(self, mock_gemini):
        mock_gemini.return_value = {
            "success": True,
            "post": "Reflecting on my recent internship experience in data analytics! Working on my first project has been an incredible learning opportunity.\n\n#DataAnalytics #MachineLearning #ContinuousLearning"
        }

        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake_test_key"}):
            res = self.client.post("/api/generate-post", json={
                "keywords": "data analytics, internship, learning, project",
                "tone": "Professional"
            })
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data["success"])
            self.assertIn("data analytics", data["post"].lower())
            self.assertIn("internship", data["post"].lower())

    # -------------------------------------------------------------
    # TEST 4: AI-generated draft edited manually -> schedule -> published
    # -------------------------------------------------------------
    @patch("requests.post")
    def test_4_ai_draft_edited_manually(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.text = '{"id": "urn:li:share:999"}'
        mock_post.return_value = mock_resp

        ai_generated_text = "Key takeaways from my first machine learning project."
        edited_text = ai_generated_text + " Grateful for the mentorship and looking forward to next steps!"

        past_time = (datetime.now() - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M")
        res = self.client.post("/schedule", data={
            "content": edited_text,
            "scheduled_at": past_time
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        # Trigger scheduler
        publish_due_posts()

        with app.app_context():
            post = Post.query.filter_by(content=edited_text).first()
            self.assertIsNotNone(post)
            self.assertEqual(post.status, "published")
            self.assertIn("Grateful for the mentorship", post.content)

    # -------------------------------------------------------------
    # TEST 5: AI generation failure -> useful error shown, app remains usable
    # -------------------------------------------------------------
    def test_5_ai_generation_failure(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": "", "OPENAI_API_KEY": ""}):
            res = self.client.post("/api/generate-post", json={
                "keywords": "some keywords",
                "tone": "Professional"
            })
            self.assertEqual(res.status_code, 400)
            data = res.get_json()
            self.assertFalse(data["success"])
            self.assertIn("AI service is not configured", data["error"])

        # Manual schedule still works seamlessly after AI failure
        res2 = self.client.post("/schedule", data={
            "content": "Manual post written directly without AI",
            "scheduled_at": datetime.now().strftime("%Y-%m-%dT%H:%M")
        }, follow_redirects=True)
        self.assertEqual(res2.status_code, 200)

    # -------------------------------------------------------------
    # TEST 6: Invalid image format -> rejected with clear message
    # -------------------------------------------------------------
    def test_6_invalid_image_format(self):
        # Fake file with invalid magic bytes (a plain text file disguised as .jpg)
        fake_bad_jpg = b"Hello, this is just plain text, not a real image!"

        res = self.client.post("/schedule", data={
            "content": "Post with corrupted file",
            "scheduled_at": datetime.now().strftime("%Y-%m-%dT%H:%M"),
            "image": (io.BytesIO(fake_bad_jpg), "fake.jpg")
        }, content_type="multipart/form-data")

        self.assertEqual(res.status_code, 400)
        self.assertIn(b"Invalid image content", res.data)

        # Unsupported extension test (.txt file)
        res_ext = self.client.post("/schedule", data={
            "content": "Post with txt file",
            "scheduled_at": datetime.now().strftime("%Y-%m-%dT%H:%M"),
            "image": (io.BytesIO(b"abc"), "document.txt")
        }, content_type="multipart/form-data")

        self.assertEqual(res_ext.status_code, 400)
        self.assertIn(b"Unsupported file format", res_ext.data)

    # -------------------------------------------------------------
    # TEST 7: Oversized image -> rejected with clear message
    # -------------------------------------------------------------
    def test_7_oversized_image(self):
        # Flask MAX_CONTENT_LENGTH is 5MB
        oversized_data = self.valid_jpeg_bytes + b"\x00" * (6 * 1024 * 1024)

        res = self.client.post("/schedule", data={
            "content": "Post with huge image",
            "scheduled_at": datetime.now().strftime("%Y-%m-%dT%H:%M"),
            "image": (io.BytesIO(oversized_data), "giant.jpg")
        }, content_type="multipart/form-data")

        self.assertEqual(res.status_code, 413)

    # -------------------------------------------------------------
    # TEST 8: No image -> existing text-only behavior remains unchanged
    # -------------------------------------------------------------
    @patch("linkedin.initialize_image_upload")
    @patch("requests.post")
    def test_8_no_image_text_only_behavior_unchanged(self, mock_post, mock_init_upload):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.text = '{"id": "urn:li:share:textonly"}'
        mock_post.return_value = mock_resp

        past_time = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
        res = self.client.post("/schedule", data={
            "content": "Purely text post without any media attachment.",
            "scheduled_at": past_time
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        # Run scheduler
        publish_due_posts()

        # Image initialization should never be called for text-only posts
        mock_init_upload.assert_not_called()

        with app.app_context():
            post = Post.query.filter_by(content="Purely text post without any media attachment.").first()
            self.assertEqual(post.status, "published")
            self.assertIsNone(post.image_path)

    # -------------------------------------------------------------
    # TEST 9: Failed LinkedIn publication -> post becomes Failed, not Published
    # -------------------------------------------------------------
    @patch("requests.post")
    def test_9_failed_linkedin_publication(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = '{"error": "Access token expired"}'
        mock_post.return_value = mock_resp

        past_time = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
        self.client.post("/schedule", data={
            "content": "This post is doomed to fail due to LinkedIn API error.",
            "scheduled_at": past_time
        }, follow_redirects=True)

        # Trigger scheduler
        publish_due_posts()

        with app.app_context():
            post = Post.query.filter_by(content="This post is doomed to fail due to LinkedIn API error.").first()
            self.assertEqual(post.status, "failed")
            self.assertIsNone(post.published_at)
            self.assertIn("HTTP 403", post.error_message)

    # -------------------------------------------------------------
    # TEST 10: Multiple scheduled posts -> Priority Queue chronological ordering
    # -------------------------------------------------------------
    @patch("requests.post")
    def test_10_multiple_scheduled_posts_priority_queue(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.text = '{"id": "urn:li:share:ordered"}'
        mock_post.return_value = mock_resp

        t_early = datetime.now() - timedelta(minutes=30)
        t_mid = datetime.now() - timedelta(minutes=20)
        t_late = datetime.now() - timedelta(minutes=10)

        processed_order = []

        def track_post_call(*args, **kwargs):
            json_body = kwargs.get("json", {})
            processed_order.append(json_body.get("commentary"))
            return mock_resp

        mock_post.side_effect = track_post_call

        with app.app_context():
            p_mid = Post(content="Mid Post (20m ago)", scheduled_at=t_mid, status="scheduled")
            p_late = Post(content="Late Post (10m ago)", scheduled_at=t_late, status="scheduled")
            p_early = Post(content="Early Post (30m ago)", scheduled_at=t_early, status="scheduled")

            db.session.add_all([p_mid, p_late, p_early])
            db.session.commit()

        # Run scheduler
        publish_due_posts()

        # Verify processed in Priority Queue order (earliest scheduled post first)
        self.assertEqual(len(processed_order), 3)
        self.assertEqual(processed_order[0], "Early Post (30m ago)")
        self.assertEqual(processed_order[1], "Mid Post (20m ago)")
        self.assertEqual(processed_order[2], "Late Post (10m ago)")

    # -------------------------------------------------------------
    # TEST 11: OAuth login initiation and state creation
    # -------------------------------------------------------------
    def test_11_oauth_initiation_and_state(self):
        res = self.client.get("/auth/linkedin")
        self.assertEqual(res.status_code, 302)
        location = res.headers.get("Location")
        self.assertTrue(location.startswith("https://www.linkedin.com/oauth/v2/authorization?"))

        parsed = urllib.parse.urlparse(location)
        params = urllib.parse.parse_qs(parsed.query)

        self.assertEqual(params["client_id"][0], "test_client_id")
        self.assertIn("w_member_social", params["scope"][0])
        state = params["state"][0]

        with app.app_context():
            oauth_rec = OAuthState.query.filter_by(state=state).first()
            self.assertIsNotNone(oauth_rec)

    # -------------------------------------------------------------
    # TEST 12: OAuth callback token exchange & profile storage
    # -------------------------------------------------------------
    @patch("requests.post")
    @patch("requests.get")
    def test_12_oauth_callback_success(self, mock_get, mock_post):
        # 1. Initiate login to generate valid state in DB
        res_init = self.client.get("/auth/linkedin")
        parsed = urllib.parse.urlparse(res_init.headers.get("Location"))
        state = urllib.parse.parse_qs(parsed.query)["state"][0]

        # 2. Mock token exchange response
        mock_tok = MagicMock()
        mock_tok.status_code = 200
        mock_tok.json.return_value = {
            "access_token": "mock_new_access_token",
            "expires_in": 5184000
        }
        mock_post.return_value = mock_tok

        # 3. Mock profile response
        mock_prof = MagicMock()
        mock_prof.status_code = 200
        mock_prof.json.return_value = {
            "sub": "new_member_999",
            "name": "Alex Tech"
        }
        mock_get.return_value = mock_prof

        # 4. Trigger callback
        res_cb = self.client.get(f"/auth/linkedin/callback?code=mock_code&state={state}")
        self.assertEqual(res_cb.status_code, 302)

        with app.app_context():
            acc = LinkedInAccount.query.filter_by(linkedin_id="new_member_999").first()
            self.assertIsNotNone(acc)
            self.assertEqual(acc.access_token, "mock_new_access_token")

            # State is cleanly deleted
            st = OAuthState.query.filter_by(state=state).first()
            self.assertIsNone(st)

    # -------------------------------------------------------------
    # TEST 13: OAuth callback failure (user cancellation / invalid state)
    # -------------------------------------------------------------
    def test_13_oauth_callback_denied_or_invalid(self):
        res_err = self.client.get("/auth/linkedin/callback?error=user_cancelled_authorize&error_description=Denied")
        self.assertEqual(res_err.status_code, 200)
        self.assertIn(b"LinkedIn authorization failed", res_err.data)

        res_bad_state = self.client.get("/auth/linkedin/callback?code=abc&state=nonexistent_state")
        self.assertEqual(res_bad_state.status_code, 200)
        self.assertIn(b"Security Error", res_bad_state.data)

    # -------------------------------------------------------------
    # TEST 14: Disconnect LinkedIn
    # -------------------------------------------------------------
    def test_14_oauth_disconnect(self):
        res = self.client.post("/auth/linkedin/disconnect")
        self.assertEqual(res.status_code, 302)
        with app.app_context():
            self.assertIsNone(LinkedInAccount.query.first())

    # -------------------------------------------------------------
    # TEST 15: Cron publish HTTP endpoint
    # -------------------------------------------------------------
    @patch("requests.post")
    def test_15_cron_publish_endpoint(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.text = '{"id": "urn:li:share:cron"}'
        mock_post.return_value = mock_resp

        past_time = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
        self.client.post("/schedule", data={
            "content": "Post published through cron trigger",
            "scheduled_at": past_time
        })

        res = self.client.get("/cron/publish")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["processed_posts"], 1)

    # -------------------------------------------------------------
    # TEST 16: Interrupted / stale publishing recovery
    # -------------------------------------------------------------
    def test_16_stale_publishing_recovery(self):
        ten_mins_ago = datetime.now() - timedelta(minutes=10)
        with app.app_context():
            interrupted_post = Post(
                content="Post crashed midway during previous run",
                scheduled_at=ten_mins_ago,
                status="publishing"
            )
            db.session.add(interrupted_post)
            db.session.commit()

        publish_due_posts()

        with app.app_context():
            recovered = Post.query.filter_by(content="Post crashed midway during previous run").first()
            self.assertEqual(recovered.status, "failed")
            self.assertIn("interrupted or timed out", recovered.error_message)

    # -------------------------------------------------------------
    # TEST 17: Dynamic Redirect URI (Render env & Forwarded headers)
    # -------------------------------------------------------------
    def test_17_dynamic_redirect_uri_render_and_headers(self):
        from app import get_linkedin_redirect_uri

        # Case A: When RENDER_EXTERNAL_URL is set in environment
        with patch.dict(os.environ, {"RENDER_EXTERNAL_URL": "https://linkedin-scheduler-prod.onrender.com"}):
            uri = get_linkedin_redirect_uri()
            self.assertEqual(uri, "https://linkedin-scheduler-prod.onrender.com/auth/linkedin/callback")

        # Case B: Behind Render reverse proxy via request headers, even if .env has localhost
        with patch.dict(os.environ, {
            "LINKEDIN_REDIRECT_URI": "http://localhost:5000/auth/linkedin/callback",
            "RENDER_EXTERNAL_URL": ""
        }):
            # Simulate a request from a live Render host
            with app.test_request_context(
                "/auth/linkedin",
                headers={
                    "X-Forwarded-Host": "my-cool-app.onrender.com",
                    "X-Forwarded-Proto": "https"
                }
            ):
                uri = get_linkedin_redirect_uri()
                self.assertEqual(uri, "https://my-cool-app.onrender.com/auth/linkedin/callback")

    # -------------------------------------------------------------
    # TEST 18: APScheduler Background Worker Lifecycle
    # -------------------------------------------------------------
    def test_18_apscheduler_initialization_and_lifecycle(self):
        from scheduler import init_scheduler, shutdown_scheduler

        # Test initializing the background scheduler
        sched = init_scheduler(app, interval_seconds=10)
        self.assertIsNotNone(sched)
        self.assertTrue(sched.running)

        # Check job is registered
        jobs = [j.id for j in sched.get_jobs()]
        self.assertIn("publish_due_posts_job", jobs)

    # -------------------------------------------------------------
    # TEST 19: AI Service candidate model fallback (404/503 handling)
    # -------------------------------------------------------------
    @patch("ai_service.genai.Client")
    def test_19_ai_service_candidate_models_fallback(self, mock_client_cls):
        from google.genai.errors import ClientError, ServerError

        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        # Mock first candidate ("gemini-3.8-flash") raising 503 ServerError
        err_503 = ServerError(503, {"error": {"message": "High demand"}}, None)
        # Mock second candidate ("gemini-2.5-flash") raising 404 ClientError
        err_404 = ClientError(404, {"error": {"message": "Model not available"}}, None)
        # Mock third candidate ("gemini-1.5-flash") raising 404 ClientError
        err_15 = ClientError(404, {"error": {"message": "Model not found"}}, None)

        # Mock fourth candidate succeeding
        mock_resp_success = MagicMock()
        mock_resp_success.text = "Crafted LinkedIn post demonstrating resilient fallback architecture."

        mock_client.models.generate_content.side_effect = [
            err_503,
            err_404,
            err_15,
            mock_resp_success
        ]

        result = ai_service.generate_with_gemini(
            api_key="mock_key",
            keywords="resilient fallback architecture",
            tone="Technical"
        )

        self.assertTrue(result["success"])
        self.assertIn("resilient fallback architecture", result["post"])
        # Verify it iterated through candidates
        self.assertEqual(mock_client.models.generate_content.call_count, 4)

    # -------------------------------------------------------------
    # TEST 20: AI Service all candidates failed -> graceful clean error
    # -------------------------------------------------------------
    @patch("ai_service.genai.Client")
    def test_20_ai_service_all_candidates_failed_graceful_response(self, mock_client_cls):
        from google.genai.errors import ServerError

        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.models.generate_content.side_effect = ServerError(503, {"error": {"message": "Busy"}}, None)

        result = ai_service.generate_with_gemini(
            api_key="mock_key",
            keywords="machine learning",
            tone="Professional"
        )

        # Must not raise an exception, must return clean failure dict
        self.assertFalse(result["success"])
        self.assertIn("AI service temporarily unavailable", result["error"])


if __name__ == "__main__":
    unittest.main()

