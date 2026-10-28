import os
from dotenv import load_dotenv
from google import genai
from google.genai.types import HttpOptions

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
            "error": "AI service is not configured. Please set GEMINI_API_KEY in your environment variables.",
        }

    prompt = build_prompt(keywords, tone)

    # List of models to try in order of preference
    models_to_try = ["gemini-3.8-flash", "gemini-1.5-flash", "gemini-2.5-flash"]
    last_error = ""

    client = genai.Client(
        api_key=api_key,
        http_options=HttpOptions(api_version="v1")
    )

    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            if response and response.text:
                return {"success": True, "post": response.text.strip()}
        except Exception as e:
            last_error = str(e)
            continue  # Try the next model if one fails or is overloaded

    return {"success": False, "error": f"Gemini API Error (All models busy): {last_error}"}