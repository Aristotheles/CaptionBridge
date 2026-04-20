from __future__ import annotations

from queue import Queue
import threading

from .audio import AZURE_SAMPLE_RATE, AudioSource, AudioStreamer


class LiveTranslationSession:
    def __init__(
        self,
        *,
        speech_key: str,
        speech_region: str,
        source_locale: str,
        target_language: str,
        source: AudioSource,
        event_queue: Queue,
    ) -> None:
        self.speech_key = speech_key.strip()
        self.speech_region = speech_region.strip()
        self.source_locale = source_locale
        self.target_language = target_language
        self.source = source
        self.event_queue = event_queue

        self._stop_event = threading.Event()
        self._recognizer = None
        self._push_stream = None
        self._audio_streamer: AudioStreamer | None = None

    @property
    def running(self) -> bool:
        return self._recognizer is not None

    def start(self) -> None:
        if not self.speech_key or not self.speech_region:
            raise RuntimeError("Azure Speech key ve region bos olamaz.")

        try:
            from azure.cognitiveservices import speech as speechsdk
        except Exception as exc:
            raise RuntimeError(
                "Azure Speech SDK yuklenemedi. Dist klasorundeki exe'yi calistirdiginizdan emin olun."
            ) from exc

        stream_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=AZURE_SAMPLE_RATE,
            bits_per_sample=16,
            channels=1,
        )
        self._push_stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)

        translation_config = speechsdk.translation.SpeechTranslationConfig(
            subscription=self.speech_key,
            region=self.speech_region,
        )
        translation_config.speech_recognition_language = self.source_locale
        translation_config.add_target_language(self.target_language)

        audio_config = speechsdk.audio.AudioConfig(stream=self._push_stream)
        self._recognizer = speechsdk.translation.TranslationRecognizer(
            translation_config=translation_config,
            audio_config=audio_config,
        )

        def recognizing(evt) -> None:
            reason = getattr(evt.result, "reason", None)
            if reason == speechsdk.ResultReason.NoMatch:
                self.event_queue.put(
                    {
                        "type": "no_match",
                        "message": "Azure ses aldi ama konusmayi eslestiremedi. Kaynak dil, ses seviyesi veya netlik yetersiz olabilir.",
                    }
                )
                return

            text = evt.result.text or ""
            translations = evt.result.translations or {}
            translated = translations.get(self.target_language, "")
            if text or translated:
                self.event_queue.put(
                    {"type": "partial", "source": text, "target": translated}
                )

        def recognized(evt) -> None:
            reason = getattr(evt.result, "reason", None)
            if reason == speechsdk.ResultReason.NoMatch:
                details = ""
                try:
                    details = str(speechsdk.NoMatchDetails.from_result(evt.result))
                except Exception:
                    details = ""
                message = "Konusma algilanmadi."
                if details:
                    message = f"{message} {details}"
                self.event_queue.put({"type": "no_match", "message": message})
                return

            text = (evt.result.text or "").strip()
            translations = evt.result.translations or {}
            translated = (translations.get(self.target_language, "") or "").strip()
            if text or translated:
                self.event_queue.put(
                    {"type": "final", "source": text, "target": translated}
                )

        def canceled(evt) -> None:
            details = getattr(evt, "error_details", "") or ""
            message = f"{evt.reason}"
            if details:
                message = f"{message} | {details}"
            self.event_queue.put({"type": "canceled", "message": message})

        def session_started(_evt) -> None:
            self.event_queue.put({"type": "status", "message": "Azure oturumu acildi. Konusma bekleniyor..."})

        def session_stopped(_evt) -> None:
            self.event_queue.put({"type": "status", "message": "Azure oturumu kapandi."})

        self._recognizer.recognizing.connect(recognizing)
        self._recognizer.recognized.connect(recognized)
        self._recognizer.canceled.connect(canceled)
        self._recognizer.session_started.connect(session_started)
        self._recognizer.session_stopped.connect(session_stopped)
        self._recognizer.start_continuous_recognition()

        self._audio_streamer = AudioStreamer(
            source=self.source,
            push_stream=self._push_stream,
            stop_event=self._stop_event,
            on_level=lambda value: self.event_queue.put({"type": "meter", "value": value}),
            on_status=lambda message: self.event_queue.put({"type": "status", "message": message}),
            on_error=lambda message: self.event_queue.put({"type": "error", "message": message}),
        )
        self._audio_streamer.start()
        self.event_queue.put({"type": "status", "message": "Dinleniyor..."})

    def stop(self) -> None:
        self._stop_event.set()

        if self._audio_streamer is not None:
            self._audio_streamer.join(timeout=2.0)
            self._audio_streamer = None

        if self._recognizer is not None:
            try:
                self._recognizer.stop_continuous_recognition()
            finally:
                self._recognizer = None

        if self._push_stream is not None:
            try:
                self._push_stream.close()
            finally:
                self._push_stream = None

        self.event_queue.put({"type": "status", "message": "Durdu."})


def run_azure_translation_test(
    *,
    speech_key: str,
    speech_region: str,
    source_locale: str,
    target_language: str,
    audio_bytes: bytes,
) -> dict[str, str]:
    if not speech_key.strip() or not speech_region.strip():
        raise RuntimeError("Azure Speech key ve region bos olamaz.")
    if not audio_bytes:
        raise RuntimeError("Azure testi icin gecerli bir ses ornegi olusmadi.")

    try:
        from azure.cognitiveservices import speech as speechsdk
    except Exception as exc:
        raise RuntimeError(
            "Azure Speech SDK yuklenemedi. Dist klasorundeki exe'yi calistirdiginizdan emin olun."
        ) from exc

    stream_format = speechsdk.audio.AudioStreamFormat(
        samples_per_second=AZURE_SAMPLE_RATE,
        bits_per_sample=16,
        channels=1,
    )
    push_stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)

    translation_config = speechsdk.translation.SpeechTranslationConfig(
        subscription=speech_key.strip(),
        region=speech_region.strip(),
    )
    translation_config.speech_recognition_language = source_locale
    translation_config.add_target_language(target_language)

    audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
    recognizer = speechsdk.translation.TranslationRecognizer(
        translation_config=translation_config,
        audio_config=audio_config,
    )
    push_stream.write(audio_bytes)
    push_stream.close()
    result = recognizer.recognize_once_async().get()
    reason = getattr(result, "reason", None)

    if reason == speechsdk.ResultReason.NoMatch:
        details = ""
        try:
            details = str(speechsdk.NoMatchDetails.from_result(result))
        except Exception:
            details = ""
        return {
            "status": "no_match",
            "message": "Azure sesi aldi ama konusma cikaramadi." + (f" {details}" if details else ""),
            "source_text": "",
            "target_text": "",
        }

    if reason == speechsdk.ResultReason.Canceled:
        message = "Azure istegi iptal edildi."
        try:
            details = speechsdk.CancellationDetails.from_result(result)
            message = f"{details.reason}"
            if details.error_details:
                message = f"{message} | {details.error_details}"
        except Exception:
            pass
        return {
            "status": "canceled",
            "message": message,
            "source_text": "",
            "target_text": "",
        }

    translations = getattr(result, "translations", {}) or {}
    source_text = (getattr(result, "text", "") or "").strip()
    target_text = (translations.get(target_language, "") or "").strip()

    if source_text or target_text:
        return {
            "status": "ok",
            "message": "Azure testi basarili.",
            "source_text": source_text,
            "target_text": target_text,
        }

    return {
        "status": "empty",
        "message": f"Azure beklenen bir sonuc donmedi. Reason={reason}",
        "source_text": "",
        "target_text": "",
    }
