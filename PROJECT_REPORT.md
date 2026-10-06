# LinkedIn Post Scheduler & AI Assistant - Project Report

## 1. Project Overview & Original Functionality

The **LinkedIn Post Scheduler** is a web-based automation tool developed to schedule, organize, and publish posts to LinkedIn personal profiles through the official LinkedIn REST API.

### 1.1 Original Core Functionality
Prior to the enhancements, the application supported:
1. **OAuth 2.0 Authentication**: Seamlessly connecting a personal LinkedIn account with scopes `openid`, `profile`, `email`, and `w_member_social`.
2. **Text Post Scheduling**: Inputting post commentary and selecting a future publication date and time.
3. **Database Persistence**: Storing scheduled posts in a SQLite database with initial status `scheduled`.
4. **Background Scheduler Engine**: A continuous background daemon checking periodically for posts whose scheduled time is reached.
5. **Direct Publishing**: Making HTTP POST requests to the LinkedIn Posts API (`/rest/posts`) to publish commentary to the member's main feed.
6. **Status Lifecycle & Dashboard**: Displaying post status (`scheduled`, `published`, `failed`) and statistical counters (Total, Scheduled, Published, Failed).

---

## 2. Professor-Suggested Enhancements

To significantly enhance productivity and visual engagement, two major enhancements were designed and integrated into the existing architecture:

### 2.1 Enhancement 1: Image / Media Post Support
Allows users to attach an image (JPG, JPEG, PNG) to any scheduled post:
- **Optional Media Attachment**: Posts can be text-only or text with an attached image.
- **Client-Side Live Preview**: Instant preview of the selected image before scheduling, with a convenient remove button.
- **Client-Side & Server-Side Security Validation**:
  - Whitelist validation of extensions (`.jpg`, `.jpeg`, `.png`).
  - Strict inspection of file signatures (magic bytes `\xff\xd8\xff` for JPEG and `\x89PNG\r\n\x1a\n` for PNG) to prevent malicious or masquerading executable files.
  - Strict 5 MB maximum file-size enforcement.
  - Random hexadecimal filename generation to eliminate directory traversal risks and file naming collisions.
- **LinkedIn 3-Step Media Publishing Workflow**: Complete implementation of the official LinkedIn Images and Posts API specifications.

### 2.2 Enhancement 2: AI-Assisted Post Generation
An integrated AI assistant that transforms rough thoughts, keywords, or bullet points into publication-ready LinkedIn drafts:
- **Prompt Engineering Constraints**: Produces authentic, coherent posts without hallucinating unprovided achievements, metrics, degrees, or corporate cliches.
- **Tone Flexibility**: Allows selecting from Professional, Casual, Technical, or Celebratory tones.
- **Non-Intrusive Workflow**: Fills the draft directly into the standard Post Content textarea, giving the user 100% editing control before scheduling.
- **Secure Backend Proxy**: Frontend JavaScript never interacts directly with third-party AI APIs. Requests pass through `/api/generate-post`, ensuring API keys remain securely hidden on the server.
- **Graceful Failure & Fallback**: If AI services are unconfigured or unavailable, an informative banner is displayed and manual post writing remains fully functional.

---

## 3. LinkedIn Media Publishing Workflow

LinkedIn's modern REST API does not accept arbitrary external image URLs. Publishing media requires a strict 3-step sequence:

```
[ Step 1: Initialize Upload ]
POST https://api.linkedin.com/rest/images?action=initializeUpload
Headers: Authorization: Bearer <Token>, Linkedin-Version: 202607, X-Restli-Protocol-Version: 2.0.0
Payload: {"initializeUploadRequest": {"owner": "urn:li:person:<ID>"}}
Response: uploadUrl & image URN ("urn:li:image:<ID>")
           ↓
[ Step 2: Binary Media Upload ]
PUT <uploadUrl>
Headers: Content-Type: image/jpeg or image/png
Body: Raw image file binary bytes
Response: HTTP 201 Created
           ↓
[ Step 3: Create Post with Media ]
POST https://api.linkedin.com/rest/posts
Payload: {
  "author": "urn:li:person:<ID>",
  "commentary": "<Post Text>",
  "visibility": "PUBLIC",
  "distribution": {"feedDistribution": "MAIN_FEED"},
  "content": {
    "media": {
      "id": "urn:li:image:<ID>"
    }
  },
  "lifecycleState": "PUBLISHED"
}
Response: HTTP 201 Created (Post Published)
```

If any step in the sequence fails, the transaction is caught, the post status is marked as `failed`, the detailed error is logged to `error_message`, and the post is never misreported as published.

---

## 4. System Architecture & Workflow

### 4.1 System Components
- **Flask Web Server (`app.py`)**: Handles routes for dashboard, OAuth callbacks, post scheduling, post deletion, and the AI post generator endpoint.
- **LinkedIn Client Module (`linkedin.py`)**: Encapsulates initialization of image uploads, binary PUT transfers, and post publishing requests.
- **AI Service Module (`ai_service.py`)**: Interacts with Google Gemini or OpenAI APIs using server-side environment variables.
- **Background Scheduler Daemon (`scheduler.py`)**: Autonomous daemon querying SQLite for due posts every 10 seconds and executing the publishing workflow.
- **Frontend Layer (`templates/dashboard.html`, `static/style.css`, `static/main.js`)**: Clean, responsive UI with real-time feedback, asynchronous AI generation, and dynamic image previewing.

### 4.2 Overall Execution Workflow

```
User visits Dashboard
   │
   ├─► [Optional] Enters keywords & tone in "AI Post Assistant"
   │        │
   │        └─► Click "Generate Post" ──► POST /api/generate-post (Backend) ──► AI Provider
   │                                                     │
   │                                                     ▼
   │                                      Populates Post Content Textarea
   │
   ├─► User reviews/edits text in Post Content
   ├─► [Optional] Uploads an image (JPG/PNG, validated client-side & server-side)
   ├─► Selects schedule date and time
   │
   └─► Clicks "Schedule Post" ──► POST /schedule
            │
            ├─► Saves image to static/uploads/ (if attached)
            └─► Saves Post in database with status="scheduled"
                     │
                     ▼
         Background Scheduler (scheduler.py)
         Polls due posts ordered by scheduled_at.asc() (Priority Queue)
                     │
            ┌────────┴────────┐
        Text Only         Has Image
            │                 │
            │                 ├─► 1. Initialize image upload (LinkedIn Images API)
            │                 ├─► 2. Upload binary bytes (PUT uploadUrl)
            │                 └─► 3. Attach image URN to post content
            │
            ▼
        Publish post to LinkedIn REST Posts API
            │
            ├─► Success (HTTP 200/201): status = "published"
            └─► Failure: status = "failed" (error_message stored)
```

---

## 5. Data Structures Mini Project Analysis

This project models key data structure paradigms in its scheduling and presentation layers:

### 5.1 Priority Queue (Min-Heap Concept) for Post Scheduling
- **Definition**: A Priority Queue is an abstract data structure where each element has an associated priority, and the element with the highest priority is served before other elements.
- **Implementation in Scheduler**:
  When querying pending posts whose scheduled time is reached, the query enforces:
  ```python
  due_posts = Post.query.filter(
      Post.status == "scheduled",
      Post.scheduled_at <= now
  ).order_by(Post.scheduled_at.asc()).all()
  ```
  Here, the **priority key** is the timestamp `scheduled_at`. The task with the earliest scheduled time (minimum timestamp) has the highest priority and is dequeued and processed first. This mirrors a **Min-Priority Queue / Min-Heap**, ensuring chronological fairness and strict deadline compliance.

### 5.2 Stack (LIFO Concept) for Post Timeline Display
- **Definition**: A Stack operates on the Last-In, First-Out (LIFO) principle, where the most recently added or updated item is accessed first.
- **Implementation in Dashboard**:
  ```python
  posts = Post.query.order_by(Post.scheduled_at.desc()).all()
  ```
  The dashboard renders scheduled and historical posts in reverse-chronological order, placing the most recently planned or published posts at the top of the feed, providing a LIFO visualization of user activities.

---

## 6. Security & Confidentiality Measures

1. **Zero Secret Exposure**:
   - LinkedIn Client ID, Client Secret, OAuth Tokens, and AI API keys are managed exclusively via environment variables (`.env`).
   - Browser JavaScript never receives or transmits API credentials.
2. **File Upload Security**:
   - Whitelist validation of extensions (`ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png"}`).
   - Binary header signature inspection (magic bytes) prevents executable uploads.
   - Filenames are randomized with `secrets.token_hex(16)`, preventing path traversal attacks.
   - Server-side maximum file size limit of 5 MB enforced.
   - Upload directory excluded from Git tracking via `.gitignore`.
3. **Database Integrity**:
   - Automatic SQLite schema migrations ensure newly added columns (`image_path`, `media_type`, `media_status`, `linkedin_media_id`) are applied smoothly without altering or corrupting existing records.

---

## 7. Verification & Test Results

A full automated test suite (`test_all.py`) was constructed and verified:

| Test Case | Scenario Description | Expected Outcome | Result |
| :--- | :--- | :--- | :--- |
| **TEST 1** | Manual text-only post scheduling | Scheduled & published via text API path | ✅ Passed |
| **TEST 2** | Text + image post scheduling | 3-step LinkedIn media upload and publishing | ✅ Passed |
| **TEST 3** | AI keyword post generation | Coherent draft generated from raw keywords | ✅ Passed |
| **TEST 4** | AI draft manually edited | Edited version scheduled and published | ✅ Passed |
| **TEST 5** | AI service failure/unconfigured | Informative error displayed; app usable | ✅ Passed |
| **TEST 6** | Invalid image format (bad magic bytes / .txt) | Rejected with HTTP 400 and clear error | ✅ Passed |
| **TEST 7** | Oversized image (> 5MB) | Rejected with HTTP 413 / size alert | ✅ Passed |
| **TEST 8** | Text-only backward compatibility | Media upload never triggered for text posts | ✅ Passed |
| **TEST 9** | Failed LinkedIn publication | Post marked as `failed`, error recorded | ✅ Passed |
| **TEST 10** | Multiple scheduled posts | Chronological Priority Queue ordering | ✅ Passed |

---

## 8. Functional Requirements & Future Scope

### 8.1 Updated Functional Requirements
- **FR-1**: User can authenticate with LinkedIn using OAuth 2.0 with social posting permissions.
- **FR-2**: User can draft posts manually or generate drafts using keywords and tone via the AI Assistant.
- **FR-3**: User can attach a valid JPG/PNG image under 5MB and preview it live.
- **FR-4**: User can schedule posts for future automatic publication.
- **FR-5**: Background scheduler publishes due posts using Priority Queue ordering.
- **FR-6**: Scheduler automatically orchestrates LinkedIn's 3-step media flow for image posts.
- **FR-7**: System logs publishing status and error messages, updating statistics on the dashboard.

### 8.2 Future Scope
- **Multi-Image & Carousel Posts**: Support LinkedIn multi-image and document carousel formats.
- **Video Post Support**: Integrate LinkedIn asynchronous video upload flow with chunked transfers.
- **Analytics & Engagement Tracking**: Track impressions, likes, comments, and shares directly within the dashboard.
- **Multi-Account Support**: Allow scheduling for multiple LinkedIn personal profiles or company organizational pages.
