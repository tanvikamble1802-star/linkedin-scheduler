import time
from datetime import datetime, timezone
import requests
from app import app, db, Post, LinkedInAccount, LINKEDIN_VERSION, LINKEDIN_POST_URL


def publish_due_posts():
    with app.app_context():
        # Get current naive local time to compare against datetime-local input
        now = datetime.now()

        # Query all scheduled posts whose scheduled time is reached or past
        due_posts = Post.query.filter(
            Post.status == "scheduled",
            Post.scheduled_at <= now
        ).all()

        if not due_posts:
            return

        account = LinkedInAccount.query.first()
        if not account:
            print("⚠️ Posts are due, but no LinkedIn account is connected!")
            return

        for post in due_posts:
            print(f"\n🚀 Processing Post ID {post.id}...")
            print(f"   Content: {post.content}")

            post_data = {
                "author": f"urn:li:person:{account.linkedin_id}",
                "commentary": post.content,
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

                print(f"   HTTP Status: {response.status_code}")
                print(f"   Response Body: {response.text}")

                if response.status_code in (200, 201):
                    post.status = "published"
                    post.published_at = datetime.now()
                    post.error_message = None
                    print(f"✅ Post ID {post.id} published successfully!")
                else:
                    post.status = "failed"
                    post.error_message = f"HTTP {response.status_code}: {response.text}"
                    print(f"❌ LinkedIn API rejected Post ID {post.id}")

            except Exception as e:
                post.status = "failed"
                post.error_message = str(e)
                print(f"❌ Exception occurred: {str(e)}")

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