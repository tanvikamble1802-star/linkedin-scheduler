import os
import requests
from dotenv import load_dotenv

load_dotenv()


def build_prompt(keywords: str, tone: str) -> str:
    return f"""You are a professional LinkedIn post assistant.
Write a clear, engaging, and professional LinkedIn post incorporating the following ideas or keywords:
"{keywords.strip()}"

Tone style: {tone}

CRITICAL RULES:
1. Combine the provided keywords naturally and coherently.
2. Adapt the writing style strictly to the requested tone ({tone}).
3. Do NOT make up specific personal achievements, company names, metrics, roles, or awards that were not mentioned by the user.
4. Avoid generic corporate buzzwords and clichés (e.g., do NOT start with "I am thrilled to announce", "Humbled and honored", etc.).
5. Keep paragraphs short and formatted cleanly with line breaks suitable for reading on LinkedIn.
6. Include at most 2-3 relevant hashtags at the very end.
7. Return ONLY the drafted post text itself. Do not include introductory remarks, commentary, explanations, or enclosing quotes.
"""


def generate_with_gemini(api_key: str, keywords: str, tone: str) -> dict:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent?key={api_key}"
    prompt = build_prompt(keywords, tone)
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}]
            }
        ],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 600
        }
    }

    try:
        # Increased timeout to 60 seconds to prevent Render connection dropouts
        response = requests.post(url, json=payload, timeout=60)
    except requests.RequestException as e:
        return {"success": False, "error": f"Failed to connect to Gemini API: {str(e)}"}

    if response.status_code != 200:
        return {
            "success": False,
            "error": f"Gemini API returned status {response.status_code}: {response.text}"
        }

    try:
        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return {"success": False, "error": "Gemini API returned no response candidates."}

        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts or "text" not in parts[0]:
            return {"success": False, "error": "Gemini API returned empty text."}

        text = parts[0]["text"].strip()
        return {"success": True, "post": text}
    except Exception as e:
        return {"success": False, "error": f"Failed to parse Gemini response: {str(e)}"}


def generate_with_openai(api_key: str, keywords: str, tone: str) -> dict:
    url = "https://api.openai.com/v1/chat/completions"
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    prompt = build_prompt(keywords, tone)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a professional LinkedIn post generator. Return only the post draft."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.7,
        "max_tokens": 600
    }

    try:
        # Increased timeout to 60 seconds
        response = requests.post(url, headers=headers, json=payload, timeout=60)
    except requests.RequestException as e:
        return {"success": False, "error": f"Failed to connect to OpenAI API: {str(e)}"}

    if response.status_code != 200:
        return {
            "success": False,
            "error": f"OpenAI API returned status {response.status_code}: {response.text}"
        }

    try:
        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            return {"success": False, "error": "OpenAI API returned no response choices."}

        text = choices[0].get("message", {}).get("content", "").strip()
        if not text:
            return {"success": False, "error": "OpenAI API returned empty text."}

        return {"success": True, "post": text}
    except Exception as e:
        return {"success": False, "error": f"Failed to parse OpenAI response: {str(e)}"}


def generate_post(keywords: str, tone: str = "Professional") -> dict:
    """
    Main entry point for AI post generation.
    Checks environment for GEMINI_API_KEY (or GOOGLE_API_KEY) or OPENAI_API_KEY.
    Returns dict with keys: 'success' (bool), and either 'post' (str) or 'error' (str).
    """
    keywords = (keywords or "").strip()
    if not keywords:
        return {"success": False, "error": "Please provide some keywords or rough ideas."}

    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    if gemini_key:
        return generate_with_gemini(gemini_key, keywords, tone)
    elif openai_key:
        return generate_with_openai(openai_key, keywords, tone)
    else:
        return {
            "success": False,
            "error": "AI service is not configured. Please set GEMINI_API_KEY or OPENAI_API_KEY in your .env file."
        }