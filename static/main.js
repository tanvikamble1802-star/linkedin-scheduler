// static/main.js - Client-side handling for AI post generation and image validation

document.addEventListener("DOMContentLoaded", function () {
    // -----------------------------------------------------------
    // FEATURE 2: AI POST GENERATION
    // -----------------------------------------------------------
    const btnGenerate = document.getElementById("btn-generate-ai");
    const btnRegenerate = document.getElementById("btn-regenerate-ai");
    const aiBtnText = document.getElementById("ai-btn-text");
    const aiKeywords = document.getElementById("ai-keywords");
    const aiTone = document.getElementById("ai-tone");
    const aiFeedback = document.getElementById("ai-feedback");
    const postContent = document.getElementById("post-content");

    async function handleAIGenerate() {
        const keywords = (aiKeywords.value || "").trim();
        const tone = (aiTone.value || "Professional").trim();

        if (!keywords) {
            showAiFeedback("⚠️ Please enter a few keywords or rough ideas above.", "warning");
            aiKeywords.focus();
            return;
        }

        // Set loading state
        setAiLoading(true);
        showAiFeedback("⏳ Drafting your LinkedIn post with AI...", "info");

        try {
            const response = await fetch("/api/generate-post", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({ keywords, tone })
            });

            let data = null;
            try {
                data = await response.json();
            } catch (jsonErr) {
                // Ignore non-json parsing error
            }

            if (response.ok && data && data.success && data.post) {
                // Populate the existing Post Content textarea (user can freely edit)
                postContent.value = data.post;
                showAiFeedback("✨ Post drafted! You can edit or refine it in the box below before scheduling.", "success");
                btnRegenerate.style.display = "inline-block";
                postContent.focus();
            } else {
                const errorMsg = (data && data.error) || (response.status === 503 ? "AI service is currently busy. Please retry in a few moments." : "Unable to generate post. You can still write your post manually.");
                showAiFeedback(`❌ ${errorMsg}`, "error");
            }
        } catch (err) {
            showAiFeedback("❌ Network error contacting AI service. Please check your connection or backend server.", "error");
        } finally {
            setAiLoading(false);
        }
    }

    function setAiLoading(isLoading) {
        if (isLoading) {
            btnGenerate.disabled = true;
            btnRegenerate.disabled = true;
            aiBtnText.textContent = "Generating...";
        } else {
            btnGenerate.disabled = false;
            btnRegenerate.disabled = false;
            aiBtnText.textContent = "Generate Post";
        }
    }

    function showAiFeedback(message, type) {
        aiFeedback.textContent = message;
        aiFeedback.className = "ai-feedback " + type;
        aiFeedback.style.display = "block";
    }

    if (btnGenerate) {
        btnGenerate.addEventListener("click", handleAIGenerate);
    }

    if (btnRegenerate) {
        btnRegenerate.addEventListener("click", handleAIGenerate);
    }

    // -----------------------------------------------------------
    // FEATURE 1: IMAGE SELECTION, VALIDATION & PREVIEW
    // -----------------------------------------------------------
    const imageUpload = document.getElementById("image-upload");
    const imagePreviewContainer = document.getElementById("image-preview-container");
    const imagePreviewImg = document.getElementById("image-preview-img");
    const imageClientError = document.getElementById("image-client-error");
    const btnRemoveImage = document.getElementById("btn-remove-image");

    const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5 MB
    const ALLOWED_EXTENSIONS = ["jpg", "jpeg", "png"];
    const ALLOWED_MIME_TYPES = ["image/jpeg", "image/png", "image/pjpeg"];

    if (imageUpload) {
        imageUpload.addEventListener("change", function () {
            imageClientError.style.display = "none";
            imageClientError.textContent = "";

            const file = this.files[0];
            if (!file) {
                hideImagePreview();
                return;
            }

            // Check file extension
            const ext = file.name.split(".").pop().toLowerCase();
            const isValidExt = ALLOWED_EXTENSIONS.includes(ext);
            const isValidMime = ALLOWED_MIME_TYPES.includes(file.type.toLowerCase()) || file.type === "";

            if (!isValidExt || !isValidMime) {
                imageUpload.value = "";
                hideImagePreview();
                showImageError("Unsupported image format. Please select a JPG, JPEG, or PNG image.");
                return;
            }

            // Check file size
            if (file.size > MAX_FILE_SIZE) {
                const sizeMb = (file.size / (1024 * 1024)).toFixed(1);
                imageUpload.value = "";
                hideImagePreview();
                showImageError(`Image is too large (${sizeMb} MB). Maximum allowed size is 5 MB.`);
                return;
            }

            // Valid image: show preview
            const reader = new FileReader();
            reader.onload = function (e) {
                imagePreviewImg.src = e.target.result;
                imagePreviewContainer.style.display = "block";
            };
            reader.readAsDataURL(file);
        });
    }

    if (btnRemoveImage) {
        btnRemoveImage.addEventListener("click", function () {
            imageUpload.value = "";
            hideImagePreview();
            imageClientError.style.display = "none";
            imageClientError.textContent = "";
        });
    }

    function showImageError(msg) {
        imageClientError.textContent = "⚠️ " + msg;
        imageClientError.style.display = "block";
    }

    function hideImagePreview() {
        imagePreviewContainer.style.display = "none";
        imagePreviewImg.src = "";
    }
});
