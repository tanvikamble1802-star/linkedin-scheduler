import os
import io
import shutil
import tempfile
import unittest
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

from app import app, db, Post, LinkedInAccount, STATIC_DIR
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


if __name__ == "__main__":
    unittest.main()
