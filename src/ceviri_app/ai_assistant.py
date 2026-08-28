from __future__ import annotations

from datetime import datetime
import json
import os

DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")


def is_potential_question_or_prompt(text: str) -> bool:
    """Verilen konusma metninin MUTLAKA bir cevap beklentisi, soru veya katki talebi icerip icermedigini sikica kontrol eder."""
    t = (text or "").strip()
    if len(t) < 5:
        return False

    lower = t.lower().strip(".,!?;: ")

    # Basit onay/dolgu ifadelerini kesinlikle yoksay
    fillers = {
        "ok", "okay", "gut", "sehr gut", "danke", "vielen dank", "ja", "nein", "genau", "richtig",
        "verstehe", "alles klar", "hallo", "guten tag", "tschüss", "auf wiedersehen", "super",
        "prima", "perfekt", "stimmt", "kein problem", "bitte", "gerne", "natürlich",
        "yes", "no", "yeah", "thanks", "thank you", "sure", "alright", "got it", "i see", "hello", "bye",
        "tamam", "evet", "hayır", "peki", "anladım", "teşekkürler", "sağol", "merhaba", "görüşürüz", "süper", "harika", "aynen"
    }
    if lower in fillers:
        return False

    # Soru işareti varsa ve en az 2 kelimeyse
    if "?" in t and len(lower.split()) >= 2:
        return True

    # Kesin soru / yöneltme / katkı talebi kalıpları
    strong_question_patterns = [
        # Almanca
        "wie ", "was ", "wo ", "wer ", "warum ", "weshalb ", "wieso ", "wann ", "welche ", "welcher ", "welches ",
        "können sie", "kannst du", "erzählen sie", "haben sie", "sind sie", "erklären sie", "beschreiben sie",
        "berichten sie", "was halten sie", "ihre meinung", "ihre erfahrung", "ihre sicht", "könnten sie",
        "wären sie", "hätten sie", "wissen sie", "haben sie erfahrung", "wie sehen sie", "wie würden sie",
        # İngilizce
        "what ", "why ", "how ", "who ", "when ", "where ", "which ", "whose ", "whom ",
        "could you", "can you", "tell me", "explain ", "describe ", "what do you think", "your experience",
        "do you ", "are you ", "have you ", "would you ", "will you ", "what is your", "how would you",
        # Türkçe
        "neden ", "nasıl ", "ne ", "kim ", "nerede ", "ne zaman ", "hangi ",
        "anlatır mısınız", "açıklar mısınız", "bahseder misiniz", "düşünüyorsunuz",
        "misiniz", "musunuz", "mısınız", "müsünüz", "görüşünüz nedir", "deneyiminiz"
    ]

    has_question_pattern = any(
        lower.startswith(p) or f" {p}" in lower or lower.endswith(p.strip())
        for p in strong_question_patterns
    )
    if has_question_pattern:
        return True

    # Soru kalıbı olmasa bile en az 7 kelime ve adaya hitap eden ("sie", "you", "siz") ifadeler
    words = lower.split()
    if len(words) >= 7:
        if "?" in t:
            return True
        direct_address = ["sie ", "ihr ", "du ", "you ", "your ", "siz ", "sizin "]
        if any(d in lower for d in direct_address):
            return True

    return False


def _build_system_instruction(
    answer_language: str, understand_language: str, profile: str = ""
) -> str:
    base = (
        "You are an expert real-time conversation and job-interview coach assisting a candidate during a LIVE interview or conversation. "
        f"The conversation is conducted in {answer_language}. "
        "You receive the other person's spoken text/question (transcribed live, which may have minor transcription noise). "
        f"Generate:\n"
        f"1. keywords: 2 to 4 quick bullet terms/hints (in {answer_language} with brief {understand_language} meaning) capturing the main point.\n"
        f"2. option_1 (Direct & Concise): A quick, confident, natural spoken answer in {answer_language} (1-2 sentences).\n"
        f"3. option_1_translation: Faithful translation of option 1 in {understand_language}.\n"
        f"4. option_2 (Detailed & Conversational): A slightly more elaborate, professional spoken response in {answer_language} (2-4 sentences).\n"
        f"5. option_2_translation: Faithful translation of option 2 in {understand_language}.\n\n"
        "Rules:\n"
        "1. First-person voice ('I', 'We'), authentic, spoken conversational tone.\n"
        "2. Do not output markdown quotes or unnecessary conversational filler unless it sounds natural when spoken.\n"
        "3. Focus on clarity, confidence, and speed."
    )
    profile = (profile or "").strip()
    if profile:
        base += (
            "\n\nUse the following background about the candidate to personalize every answer "
            "(stay truthful to it, never invent facts that contradict it):\n"
            f"{profile}"
        )
    return base


def format_prompter_card(
    question_num: int,
    time_str: str,
    question: str,
    keywords: str,
    opt1: str,
    opt1_tr: str,
    opt2: str,
    opt2_tr: str,
) -> str:
    """Canli mulakat prompteri icin okunmasi en hizli kart formatini olusturur."""
    card = (
        f"═══════════════════════════════════════════════════════════════\n"
        f"🟢 [SORU #{question_num} • {time_str}] \"{question}\"\n"
    )
    if keywords:
        card += f"🔑 İpucu: {keywords}\n"
    card += (
        f"───────────────────────────────────────────────────────────────\n"
        f"👉 1. SEÇENEK (Kısa & Doğrudan):\n"
        f"\"{opt1}\"\n"
        f"(🇹🇷 {opt1_tr})\n\n"
        f"👉 2. SEÇENEK (Alternatif & Detaylı):\n"
        f"\"{opt2}\"\n"
        f"(🇹🇷 {opt2_tr})\n"
        f"═══════════════════════════════════════════════════════════════\n\n"
    )
    return card


def test_gemini_connection(
    *,
    api_key: str,
    model: str = "",
    timeout: float = 15.0,
) -> dict[str, str]:
    """Gemini API anahtarini ve model erisimini hizli bir sorgu ile test eder."""
    api_key = (api_key or "").strip()
    if not api_key:
        raise RuntimeError("Gemini API key bos olamaz.")
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
        response = client.models.generate_content(
            model=model,
            contents="Say 'OK' to confirm the API connection is active.",
            config=types.GenerateContentConfig(
                max_output_tokens=20,
                http_options=types.HttpOptions(timeout=int(timeout * 1000)),
            ),
        )
        reply = (response.text or "").strip()
        return {
            "status": "success",
            "model": model,
            "reply": reply or "OK",
        }
    except Exception as exc:
        msg = str(exc)
        if "API_KEY_INVALID" in msg or "API key not valid" in msg or ("400" in msg and "API_KEY" in msg):
            raise RuntimeError("Gemini API key gecersiz veya yetkisiz.") from exc
        if "NOT_FOUND" in msg or "404" in msg or ("models/" in msg and "not found" in msg.lower()):
            raise RuntimeError(
                f"'{model}' modeli bulunamadi. Model adini kontrol edin (orn. gemini-2.5-flash, gemini-2.0-flash, gemini-1.5-flash)."
            ) from exc
        if "quota" in msg.lower() or "429" in msg or "RESOURCE_EXHAUSTED" in msg:
            raise RuntimeError("Gemini kota/hiz limiti asildi (429). Lutfen biraz bekleyin veya kotanizi kontrol edin.") from exc
        if "getaddrinfo" in msg or "connection" in msg.lower() or "network" in msg.lower() or "timeout" in msg.lower():
            raise RuntimeError(
                "Gemini sunucusuna baglanilamadi. Internet baglantinizi, DNS veya VPN/proxy ayarlarinizi kontrol edin."
            ) from exc
        raise RuntimeError(f"Gemini hatasi: {msg[:300]}") from exc


def generate_interview_answer(
    *,
    api_key: str,
    question: str,
    answer_language: str,
    understand_language: str,
    model: str = "",
    profile: str = "",
    question_num: int = 1,
    timeout: float = 20.0,
) -> dict[str, str]:
    """Gemini SDK ile konusma/mulakat sorusuna 2 dogal secenek, anahtar kelimeler ve cevirilerini uretir."""

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
                "keywords": {
                    "type": "string",
                    "description": "2-4 key bullet hints/words",
                },
                "option_1": {
                    "type": "string",
                    "description": "Option 1 (Direct & Concise) in answer_language",
                },
                "option_1_translation": {
                    "type": "string",
                    "description": "Option 1 translation in understand_language",
                },
                "option_2": {
                    "type": "string",
                    "description": "Option 2 (Detailed & Conversational) in answer_language",
                },
                "option_2_translation": {
                    "type": "string",
                    "description": "Option 2 translation in understand_language",
                },
            },
            "required": ["keywords", "option_1", "option_1_translation", "option_2", "option_2_translation"],
        }

        response = client.models.generate_content(
            model=model,
            contents=f"Spoken input / Question:\n{question}",
            config=types.GenerateContentConfig(
                system_instruction=_build_system_instruction(
                    answer_language, understand_language, profile
                ),
                temperature=0.35,
                response_mime_type="application/json",
                response_schema=response_schema,
                http_options=types.HttpOptions(timeout=int(timeout * 1000)),
            ),
        )
    except Exception as exc:
        msg = str(exc)
        if "API_KEY_INVALID" in msg or "API key" in msg.lower():
            raise RuntimeError("Gemini API key gecersiz veya yetkisiz.") from exc
        if "NOT_FOUND" in msg or "404" in msg or ("models/" in msg and "not found" in msg.lower()):
            raise RuntimeError(
                f"'{model}' modeli bulunamadi. Model adini kontrol edin (orn. gemini-2.5-flash, gemini-2.0-flash, gemini-1.5-flash)."
            ) from exc
        if "quota" in msg.lower() or "429" in msg:
            raise RuntimeError("Gemini kota/hiz limiti asildi. Biraz bekleyin.") from exc
        if "getaddrinfo" in msg or "connection" in msg.lower() or "network" in msg.lower():
            raise RuntimeError(
                "Gemini'ye baglanilamadi. Internet baglantinizi ve VPN/proxy ayarlarinizi kontrol edin."
            ) from exc
        raise RuntimeError(f"Gemini hatasi: {msg[:300]}") from exc

    try:
        parsed = json.loads(response.text)
        keywords = (parsed.get("keywords") or "").strip()
        opt1 = (parsed.get("option_1") or "").strip()
        opt1_tr = (parsed.get("option_1_translation") or "").strip()
        opt2 = (parsed.get("option_2") or "").strip()
        opt2_tr = (parsed.get("option_2_translation") or "").strip()

        time_now = datetime.now().strftime("%H:%M")
        prompter_card = format_prompter_card(
            question_num=question_num,
            time_str=time_now,
            question=question.split("\n")[0],
            keywords=keywords,
            opt1=opt1,
            opt1_tr=opt1_tr,
            opt2=opt2,
            opt2_tr=opt2_tr,
        )

        answer = f"🔹 [1. SEÇENEK - Doğrudan & Net]:\n{opt1}\n\n🔸 [2. SEÇENEK - Alternatif & Detaylı]:\n{opt2}"
        answer_translation = f"🔹 [1. SEÇENEK ÇEVİRİSİ]:\n{opt1_tr}\n\n🔸 [2. SEÇENEK ÇEVİRİSİ]:\n{opt2_tr}"
    except Exception:
        prompter_card = (response.text or "").strip()
        answer = prompter_card
        answer_translation = ""
        opt1, opt1_tr, opt2, opt2_tr, keywords = "", "", "", "", ""

    return {
        "prompter_card": prompter_card,
        "answer": answer,
        "answer_translation": answer_translation,
        "opt1": opt1,
        "opt1_tr": opt1_tr,
        "opt2": opt2,
        "opt2_tr": opt2_tr,
        "keywords": keywords,
    }
