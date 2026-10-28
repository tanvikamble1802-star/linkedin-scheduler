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


def generate_post(keywords: str, tone: str = "Professional") -> dict:
    keywords = (keywords or "").strip()
    if not keywords:
        return {"success": False, "error": "Please provide some keywords or rough ideas."}

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return {
            "success": False,
            "error": "AI service is not configured. Please set GEMINI_API_KEY in your environment variables."
        }

    prompt = build_prompt(keywords, tone)
    
    # Target models in priority order
    candidate_models = ["gemini-3.8-flash", "gemini-2.5-flash", "gemini-1.5-flash"]
    last_error_msg = ""

    for model_name in candidate_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ]
        }

        try:
            response = requests.post(url, json=payload, timeout=30)
            data = response.json()

            if response.status_code == 200:
                try:
                    generated_text = data["candidates"][0]["content"]["parts"][0]["text"]
                    return {"success": True, "post": generated_text.strip()}
                except (KeyError, IndexError):
                    last_error_msg = "Received empty response structure from Gemini API."
                    continue

            # If model is unavailable or returned error, capture message and attempt next model
            error_data = data.get("error", {})
            last_error_msg = error_data.get("message", f"HTTP {response.status_code}")
            
        except Exception as e:
            last_error_msg = str(e)
            continue

    return {"success": False, "error": f"AI Generation Failed: {last_error_msg}"}