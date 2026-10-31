import os
from dotenv import load_dotenv
import requests

from google import genai
from google.genai import types
from google.genai import errors as genai_errors

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


def extract_text_from_response(response) -> str:
    """
    Safely extracts generated text from a google-genai response object.
    """
    if response is None:
        return ""

    # Primary: .text attribute
    if hasattr(response, "text") and response.text:
        return response.text.strip()

    # Fallback: inspect candidates and content parts
    candidates = getattr(response, "candidates", None)
    if candidates and len(candidates) > 0:
        candidate = candidates[0]
        content = getattr(candidate, "content", None)
        if content and hasattr(content, "parts"):
            text_parts = [
                part.text.strip()
                for part in content.parts
                if hasattr(part, "text") and part.text
            ]
            if text_parts:
                return "\n\n".join(text_parts).strip()

    return ""


def generate_with_gemini(api_key: str, keywords: str, tone: str = "Professional") -> dict:
    """
    Generates a LinkedIn post using the official google-genai SDK.
    Implements a resilient fallback chain across candidate models:
      - "gemini-3.8-flash"
      - "gemini-2.5-flash"
      - "gemini-1.5-flash"
      plus extended fallbacks ("gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-flash-latest")
    Catches 503 high-demand, 504 timeouts, 404 version/deprecation, and 429 quota errors gracefully.
    """
    prompt = build_prompt(keywords, tone)

    # Requested candidate models followed by resilient production fallbacks
    candidate_models = [
        "gemini-3.8-flash",
        "gemini-2.5-flash",
        "gemini-1.5-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-flash-latest",
        "gemini-flash-lite-latest"
    ]

    last_error_message = "No models available"

    try:
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=12000)
        )
    except Exception as init_err:
        return {
            "success": False,
            "error": f"Failed to initialize Gemini AI client: {str(init_err)}"
        }

    config = types.GenerateContentConfig(
        temperature=0.7,
        max_output_tokens=600
    )

    for model_name in candidate_models:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config
            )

            generated_text = extract_text_from_response(response)
            if generated_text:
                return {
                    "success": True,
                    "post": generated_text,
                    "model_used": model_name
                }
            else:
                last_error_message = f"Model {model_name} returned an empty response."

        except genai_errors.APIError as api_err:
            code = getattr(api_err, "code", None)
            msg = getattr(api_err, "message", str(api_err))

            # Distinguish transient or version errors
            if code == 503 or "high demand" in msg.lower() or "overloaded" in msg.lower():
                last_error_message = f"Gemini is experiencing high demand ({model_name})"
            elif code == 504 or "deadline" in msg.lower():
                last_error_message = f"Request timed out ({model_name})"
            elif code == 404 or "not found" in msg.lower() or "no longer available" in msg.lower():
                last_error_message = f"Model version unavailable ({model_name})"
            elif code == 429:
                last_error_message = "Rate limit reached"
            else:
                last_error_message = f"Gemini error ({model_name}): {msg}"

            continue

        except Exception as exc:
            last_error_message = f"Connection error ({model_name}): {str(exc)}"
            continue

    return {
        "success": False,
        "error": f"AI service temporarily unavailable ({last_error_message}). Please try again shortly or write your post manually."
    }


def generate_with_openai(api_key: str, keywords: str, tone: str = "Professional") -> dict:
    """
    Fallback provider using OpenAI Chat Completions API.
    """
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
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        data = response.json()

        if response.status_code == 200:
            choices = data.get("choices", [])
            if choices:
                text = choices[0].get("message", {}).get("content", "").strip()
                if text:
                    return {"success": True, "post": text}
            return {"success": False, "error": "OpenAI returned an empty response."}

        error_msg = data.get("error", {}).get("message", f"HTTP {response.status_code}")
        return {"success": False, "error": f"OpenAI Error: {error_msg}"}
    except Exception as e:
        return {"success": False, "error": f"Failed to connect to OpenAI API: {str(e)}"}


def generate_post(keywords: str, tone: str = "Professional") -> dict:
    """
    Main entry point for AI post generation.
    Checks environment for GEMINI_API_KEY (or GOOGLE_API_KEY) or OPENAI_API_KEY.
    Returns dict with keys: 'success' (bool), and either 'post' (str) or 'error' (str).
    Guarantees no unhandled exceptions are raised to the caller.
    """
    try:
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
                "error": "AI service is not configured. Please set GEMINI_API_KEY in your environment variables."
            }
    except Exception as e:
        return {
            "success": False,
            "error": f"Unexpected error during AI generation: {str(e)}"
        }