from __future__ import annotations

import os

DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")


def _build_system_instruction(
    answer_language: str, understand_language: str, profile: str = ""
) -> str:
    base = (
        "You are an expert job-interview coach assisting a candidate during a LIVE interview. "
        f"The interview is conducted in {answer_language}. "
        "You receive the interviewer's question (transcribed live, so it may be slightly noisy). "
        f"Produce the BEST possible answer the candidate should say out loud, written in {answer_language}. "
        "The answer must be natural, spoken, first-person, confident and concise "
        "(2-5 sentences unless the question clearly needs more). Do not add labels or quotation marks. "
        f"Also provide a faithful {understand_language} translation of that same answer so the candidate "
        "understands what they are about to say. "
        "If the input is not a real question or is unintelligible, still give a safe, polite spoken response."
    )
    profile = (profile or "").strip()
    if profile:
        base += (
            "\n\nUse the following background about the candidate to personalize every answer "
            "(stay truthful to it, never invent facts that contradict it):\n"
            f"{profile}"
        )
    return base


def generate_interview_answer(
    *,
    api_key: str,
    question: str,
    answer_language: str,
    understand_language: str,
    model: str = "",
    profile: str = "",
    timeout: float = 20.0,
) -> dict[str, str]:
    """Gemini SDK ile mulakat sorusuna en iyi cevabi (answer_language) ve cevirisini (understand_language) alir."""

    api_key = (api_key or "").strip()
    if not api_key:
        raise RuntimeError("Gemini API key bos olamaz.")
    question = (question or "").strip()
    if not question:
        raise RuntimeError("Cevap uretmek icin once bir soru algilanmali.")
    model = (model or "").strip() or DEFAULT_GEMINI_MODEL

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError(
            "google-genai paketi bulunamadi. 'pip install google-genai' komutunu calistirin."
        ) from exc

    try:
        client = genai.Client(api_key=api_key)

        response_schema = {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "answer_translation": {"type": "string"},
            },
            "required": ["answer", "answer_translation"],
        }

        response = client.models.generate_content(
            model=model,
            contents=f"Interview question:\n{question}",
            config=types.GenerateContentConfig(
                system_instruction=_build_system_instruction(
                    answer_language, understand_language, profile
                ),
                temperature=0.4,
                response_mime_type="application/json",
                response_schema=response_schema,
                http_options=types.HttpOptions(timeout=int(timeout * 1000)),
            ),
        )
    except Exception as exc:
        msg = str(exc)
        if "API_KEY_INVALID" in msg or "API key" in msg.lower():
            raise RuntimeError("Gemini API key gecersiz veya yetkisiz.") from exc
        if "quota" in msg.lower() or "429" in msg:
            raise RuntimeError("Gemini kota/hiz limiti asildi. Biraz bekleyin.") from exc
        if "getaddrinfo" in msg or "connection" in msg.lower() or "network" in msg.lower():
            raise RuntimeError(
                "Gemini'ye baglanilamadi. Internet baglantinizi ve VPN/proxy ayarlarinizi kontrol edin."
            ) from exc
        raise RuntimeError(f"Gemini hatasi: {msg[:300]}") from exc

    try:
        import json
        parsed = json.loads(response.text)
        answer = (parsed.get("answer") or "").strip()
        answer_translation = (parsed.get("answer_translation") or "").strip()
    except Exception:
        answer = (response.text or "").strip()
        answer_translation = ""

    if not answer and not answer_translation:
        raise RuntimeError("Gemini bos bir cevap dondurdu.")

    return {"answer": answer, "answer_translation": answer_translation}
