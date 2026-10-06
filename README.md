# LinkedIn Post Scheduler & AI Assistant

An automated LinkedIn post scheduling and generation web application built with **Flask**, **SQLite / SQLAlchemy**, **LinkedIn REST API**, and **AI Post Assistant**.

This project provides end-to-end management for creating, drafting with AI, previewing, and scheduling both text and image posts to LinkedIn.

---

## 🚀 Key Features

1. **LinkedIn OAuth 2.0 Integration**:
   - Secure authorization code flow with OpenID Connect (`openid profile email w_member_social`).
   - Secure member identification via person URN (`urn:li:person:...`).

2. **Text & Image Post Scheduling**:
   - Schedule text-only posts or posts with attached images.
   - Client-side & server-side validation (formats: JPG, JPEG, PNG; max size: 5MB).
   - Real-time client-side image preview before scheduling.
   - Automatic 3-step LinkedIn media publishing workflow (Initialize Upload -> Binary PUT -> Create Post).

3. **AI-Assisted Post Generation (✨ AI Assistant)**:
   - Transform rough keywords or ideas into engaging LinkedIn posts.
   - Configurable tones: `Professional`, `Casual`, `Technical`, `Celebratory`.
   - Client-side JavaScript calls a secure backend API (`/api/generate-post`).
   - Strict adherence to prompt constraints (no fake achievements, no generic clichés, maximum 2-3 hashtags).
   - User maintains full manual editing control before scheduling.
   - Graceful fallback: manual writing is never blocked if AI is unavailable or unconfigured.

4. **Background Priority Scheduler**:
   - Background worker processes due posts automatically.
   - **Priority Queue Implementation**: Posts are ordered by `scheduled_at.asc()`, giving earlier scheduled posts higher processing priority (Min-Heap principle).
   - **LIFO History**: Dashboard displays posts in reverse-chronological order (`scheduled_at.desc()`).
   - Automatic status tracking: `Scheduled`, `Published`, and `Failed` with error message logging.

---

## 🛠️ Technology Stack

- **Backend**: Python 3.10+, Flask 3.1, Flask-SQLAlchemy 3.1, SQLite 3
- **Frontend**: Semantic HTML5, Vanilla CSS3 (custom LinkedIn-inspired palette), Vanilla JavaScript
- **APIs**:
  - LinkedIn REST Posts API (`/rest/posts`)
  - LinkedIn REST Images API (`/rest/images?action=initializeUpload`)
  - Google Gemini API / OpenAI API (secure backend integration)

---

## ⚙️ Environment Configuration

Create a `.env` file in the project root directory (do not commit this file to version control):

```env
# LinkedIn Developer Application Credentials
LINKEDIN_CLIENT_ID=your_linkedin_client_id
LINKEDIN_CLIENT_SECRET=your_linkedin_client_secret
LINKEDIN_REDIRECT_URI=http://localhost:5000/auth/linkedin/callback
LINKEDIN_VERSION=202607

# Flask Session Key
SECRET_KEY=your_secure_random_flask_secret_key

# AI Provider API Keys (Configure at least one for AI Post Generation)
GEMINI_API_KEY=your_google_gemini_api_key
# Alternatively:
# OPENAI_API_KEY=your_openai_api_key
# OPENAI_MODEL=gpt-4o-mini
```

---

## 🏃 Running the Application

### 1. Activate the Virtual Environment
On Windows (PowerShell):
```powershell
.\venv\Scripts\Activate.ps1
```

### 2. Run the Flask Web Application
```powershell
python app.py
```
The application will be accessible at: `http://127.0.0.1:5000`

### 3. Run the Background Scheduler
In a separate terminal:
```powershell
python scheduler.py
```
The scheduler checks for due posts every 10 seconds and publishes them to LinkedIn.

---

## 🧪 Running Automated Tests

To run the complete automated test suite covering all 10 core verification scenarios:
```powershell
$env:PYTHONIOENCODING="utf-8"; python test_all.py
```

### Verified Test Cases:
1. `test_1_manual_text_only_post`: Manual text post scheduling and publishing.
2. `test_2_text_plus_image_post`: Image post scheduling and 3-step LinkedIn media publishing workflow.
3. `test_3_ai_keyword_generation`: AI draft generation from raw keywords.
4. `test_4_ai_draft_edited_manually`: Editing AI drafts manually and publishing.
5. `test_5_ai_generation_failure`: Graceful error handling when AI is unavailable.
6. `test_6_invalid_image_format`: Rejection of corrupted files and unsupported formats.
7. `test_7_oversized_image`: Rejection of files exceeding 5MB.
8. `test_8_no_image_text_only_behavior_unchanged`: Verification that text-only path is untouched.
9. `test_9_failed_linkedin_publication`: Error recording and marking failed posts.
10. `test_10_multiple_scheduled_posts_priority_queue`: Chronological Priority Queue ordering.
