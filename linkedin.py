import os
import requests

LINKEDIN_IMAGES_URL = "https://api.linkedin.com/rest/images?action=initializeUpload"
LINKEDIN_POST_URL = "https://api.linkedin.com/rest/posts"
DEFAULT_LINKEDIN_VERSION = os.getenv("LINKEDIN_VERSION", "202607")


def initialize_image_upload(access_token: str, linkedin_id: str, version: str = None) -> tuple[str, str]:
    """
    Step 1 of LinkedIn Image Posting:
    Registers the intent to upload an image and receives an uploadUrl and image URN.
    """
    version = version or DEFAULT_LINKEDIN_VERSION
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "Linkedin-Version": version
    }

    payload = {
        "initializeUploadRequest": {
            "owner": f"urn:li:person:{linkedin_id}"
        }
    }

    response = requests.post(
        LINKEDIN_IMAGES_URL,
        headers=headers,
        json=payload,
        timeout=25
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Failed to initialize image upload (HTTP {response.status_code}): {response.text}"
        )

    data = response.json()
    value = data.get("value", {})
    upload_url = value.get("uploadUrl")
    image_urn = value.get("image")

    if not upload_url or not image_urn:
        raise ValueError(
            f"LinkedIn image initialization response did not contain uploadUrl or image URN: {data}"
        )

    return upload_url, image_urn


def upload_image_binary(upload_url: str, file_path_or_bytes, content_type: str = "application/octet-stream") -> requests.Response:
    """
    Step 2 of LinkedIn Image Posting:
    Uploads raw image bytes to the pre-signed uploadUrl via an HTTP PUT request.
    """
    if isinstance(file_path_or_bytes, (str, bytes, bytearray)):
        if isinstance(file_path_or_bytes, str):
            with open(file_path_or_bytes, "rb") as f:
                image_data = f.read()
        else:
            image_data = file_path_or_bytes
    else:
        image_data = file_path_or_bytes.read()

    headers = {
        "Content-Type": content_type or "application/octet-stream"
    }

    response = requests.put(
        upload_url,
        data=image_data,
        headers=headers,
        timeout=40
    )

    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"Failed to upload binary image data to LinkedIn (HTTP {response.status_code}): {response.text}"
        )

    return response


def publish_post(access_token: str, linkedin_id: str, commentary: str, media_id: str = None, version: str = None) -> requests.Response:
    """
    Publishes a post (text-only or with attached media) to LinkedIn using the REST Posts API.
    """
    version = version or DEFAULT_LINKEDIN_VERSION
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "Linkedin-Version": version
    }

    post_data = {
        "author": f"urn:li:person:{linkedin_id}",
        "commentary": commentary,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": []
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False
    }

    # Attach media if provided (e.g. urn:li:image:...)
    if media_id:
        post_data["content"] = {
            "media": {
                "id": media_id
            }
        }

    response = requests.post(
        LINKEDIN_POST_URL,
        headers=headers,
        json=post_data,
        timeout=25
    )

    return response
