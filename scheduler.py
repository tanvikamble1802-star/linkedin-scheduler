import os
import sys
import time
from datetime import datetime
import requests
from app import app, db, Post, LinkedInAccount, LINKEDIN_VERSION, STATIC_DIR
from linkedin import initialize_image_upload, upload_image_binary, publish_post

# Ensure UTF-8 output encoding on Windows consoles
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def publish_due_posts():
    with app.app_context():
        # Get current naive local time to compare against datetime-local input
        now = datetime.now()

        # Query all scheduled posts whose scheduled time is reached or past
        # Priority Queue principle: Earlier scheduled posts receive higher processing priority
        due_posts = Post.query.filter(
            Post.status == "scheduled",
            Post.scheduled_at <= now
        ).order_by(Post.scheduled_at.asc()).all()

        if not due_posts:
            return

        account = LinkedInAccount.query.first()
        if not account:
            print("⚠️ Posts are due, but no LinkedIn account is connected!")
            return

        for post in due_posts:
            print(f"\n🚀 Processing Post ID {post.id}...")
            print(f"   Content: {post.content}")

            try:
                media_id = None
                if post.image_path:
                    print(f"   Attached image: {post.image_path}")
                    image_full_path = os.path.join(STATIC_DIR, post.image_path)
                    if not os.path.exists(image_full_path):
                        raise FileNotFoundError(f"Attached image file not found on server: {post.image_path}")

                    # Step 1: Initialize image upload with LinkedIn
                    print("   Step 1: Initializing image upload with LinkedIn...")
                    upload_url, image_urn = initialize_image_upload(
                        access_token=account.access_token,
                        linkedin_id=account.linkedin_id,
                        version=LINKEDIN_VERSION
                    )
                    post.linkedin_media_id = image_urn

                    # Step 2: Upload image binary via PUT to the pre-signed uploadUrl
                    print(f"   Step 2: Uploading image binary to LinkedIn ({image_urn})...")
                    content_type = post.media_type or "application/octet-stream"
                    upload_image_binary(
                        upload_url=upload_url,
                        file_path_or_bytes=image_full_path,
                        content_type=content_type
                    )
                    media_id = image_urn
                    post.media_status = "uploaded"

                # Step 3: Create post on LinkedIn (text or with attached media)
                print("   Step 3: Creating post on LinkedIn...")
                response = publish_post(
                    access_token=account.access_token,
                    linkedin_id=account.linkedin_id,
                    commentary=post.content,
                    media_id=media_id,
                    version=LINKEDIN_VERSION
                )

                print(f"   HTTP Status: {response.status_code}")
                print(f"   Response Body: {response.text}")

                if response.status_code in (200, 201):
                    post.status = "published"
                    post.published_at = datetime.now()
                    post.error_message = None
                    if post.image_path:
                        post.media_status = "published"
                    print(f"✅ Post ID {post.id} published successfully!")
                else:
                    post.status = "failed"
                    if post.image_path:
                        post.media_status = "failed"
                    post.error_message = f"HTTP {response.status_code}: {response.text}"
                    print(f"❌ LinkedIn API rejected Post ID {post.id}")

            except Exception as e:
                post.status = "failed"
                if post.image_path:
                    post.media_status = "failed"
                post.error_message = str(e)
                print(f"❌ Exception occurred for Post ID {post.id}: {str(e)}")

            # Commit status update per post inside the loop
            db.session.commit()


if __name__ == "__main__":
    print("⏰ Post scheduler started. Checking every 10 seconds...")
    while True:
        try:
            publish_due_posts()
        except Exception as err:
            print(f"❌ Scheduler loop error: {err}")
        time.sleep(10)
