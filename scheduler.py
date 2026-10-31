import os
import sys
import threading
import atexit
from datetime import datetime, timedelta
import requests

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

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

# Global APScheduler instance & synchronization lock
_scheduler = None
_scheduler_lock = threading.Lock()


def publish_due_posts(flask_app=None) -> int:
    """
    Scans for scheduled posts that are due, transitions them through
    'publishing' -> 'published' or 'failed', and executes the LinkedIn publishing workflow.
    Returns the count of processed posts.
    """
    from app import app as default_app, db, Post, LinkedInAccount, LINKEDIN_VERSION, STATIC_DIR

    target_app = flask_app or default_app

    with target_app.app_context():
        now = datetime.now()

        # Step 0: Recovery - reset any posts stuck in 'publishing' for > 5 minutes (process interruption/restart)
        stale_cutoff = now - timedelta(minutes=5)
        stale_posts = Post.query.filter(
            Post.status == "publishing",
            Post.scheduled_at <= stale_cutoff
        ).all()
        for sp in stale_posts:
            sp.status = "failed"
            sp.error_message = "Publishing process was interrupted or timed out. You may reschedule this post."
        if stale_posts:
            db.session.commit()

        # Step 1: Query all scheduled posts whose scheduled time is reached or past
        # Priority Queue principle: Earlier scheduled posts receive higher processing priority
        due_posts = Post.query.filter(
            Post.status == "scheduled",
            Post.scheduled_at <= now
        ).order_by(Post.scheduled_at.asc()).all()

        if not due_posts:
            return 0

        account = LinkedInAccount.query.first()
        if not account:
            print("⚠️ [Scheduler] Posts are due, but no LinkedIn account is connected!")
            return 0

        processed_count = 0

        for post in due_posts:
            # Atomic state transition: mark post as 'publishing' to prevent concurrent execution
            post.status = "publishing"
            post.error_message = None
            db.session.commit()

            print(f"\n🚀 [Scheduler] Processing Post ID {post.id}...")
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

                    # Extract LinkedIn post ID from RestLi headers or response JSON
                    raw_id = response.headers.get("x-restli-id") if hasattr(response, "headers") else None
                    if isinstance(raw_id, str) and raw_id.strip():
                        post.linkedin_post_id = raw_id.strip()
                    else:
                        try:
                            json_data = response.json() if hasattr(response, "json") else {}
                            json_id = json_data.get("id") if isinstance(json_data, dict) else None
                            post.linkedin_post_id = str(json_id).strip() if isinstance(json_id, str) else None
                        except Exception:
                            post.linkedin_post_id = None

                    if post.image_path:
                        post.media_status = "published"
                    print(f"✅ Post ID {post.id} published successfully! (LinkedIn ID: {post.linkedin_post_id})")
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
            processed_count += 1

        return processed_count


def init_scheduler(flask_app=None, interval_seconds=15):
    """
    Initializes and starts the APScheduler BackgroundScheduler within the Gunicorn
    worker or Flask process lifecycle, ensuring safe single-instance background execution.
    """
    global _scheduler

    with _scheduler_lock:
        if _scheduler is not None and _scheduler.running:
            return _scheduler

        # In Werkzeug local debug mode, the parent process should not run the scheduler
        if os.environ.get("WERKZEUG_RUN_MAIN") == "false":
            return None

        scheduler = BackgroundScheduler(daemon=True)

        def scheduled_job():
            try:
                publish_due_posts(flask_app)
            except Exception as err:
                print(f"⚠️ [APScheduler] Error executing scheduled job: {err}", flush=True)

        scheduler.add_job(
            func=scheduled_job,
            trigger=IntervalTrigger(seconds=interval_seconds),
            id="publish_due_posts_job",
            name="Auto-publish due LinkedIn posts",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60
        )

        scheduler.start()
        _scheduler = scheduler
        print(f"🚀 [APScheduler] Background worker started (polling every {interval_seconds}s)", flush=True)

        # Register graceful shutdown on process termination
        atexit.register(shutdown_scheduler)
        return _scheduler


def shutdown_scheduler():
    """
    Gracefully shuts down the background scheduler when worker process exits.
    """
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None and _scheduler.running:
            try:
                _scheduler.shutdown(wait=False)
                print("🛑 [APScheduler] Background worker stopped cleanly.", flush=True)
            except Exception:
                pass
            _scheduler = None


if __name__ == "__main__":
    from apscheduler.schedulers.blocking import BlockingScheduler
    print("⏰ [APScheduler] Standalone worker started. Polling every 10 seconds...", flush=True)
    blocking_sched = BlockingScheduler()
    blocking_sched.add_job(
        func=publish_due_posts,
        trigger="interval",
        seconds=10,
        id="standalone_publish_due_posts",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60
    )
    try:
        blocking_sched.start()
    except (KeyboardInterrupt, SystemExit):
        print("\n🛑 [APScheduler] Standalone worker stopped.")
