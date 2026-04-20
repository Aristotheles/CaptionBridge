from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import os
import re
import queue
import tempfile
import threading
from typing import Callable
import wave

import numpy as np
import soundcard as sc
import sounddevice as sd


AZURE_SAMPLE_RATE = 16000
BLOCK_SECONDS = 0.10
DEBUG_DIR = os.path.join(tempfile.gettempdir(), "CeviriApp")
DEBUG_LOG_PATH = os.path.join(DEBUG_DIR, "audio-debug.log")

MODE_MICROPHONE = "microphone"
MODE_SYSTEM = "system"
MODE_VIRTUAL = "virtual"
MODE_CHOICES = (
    (MODE_SYSTEM, "Sistem Sesi"),
    (MODE_VIRTUAL, "Sanal Aygit"),
    (MODE_MICROPHONE, "Mikrofon"),
)
MODE_LABELS = dict(MODE_CHOICES)
LABEL_TO_MODE = {label: mode for mode, label in MODE_CHOICES}

VIRTUAL_MARKERS = (
    "virtual",
    "vb-audio",
    "vb cable",
    "cable",
    "loopback",
    "hitpaw",
    "blackhole",
)
SYSTEM_CAPTURE_MARKERS = (
    "stereo mix",
    "stereo kar",
    "karis",
    "karış",
    "what u hear",
    "wave out",
    "hoparl",
    "speaker",
    "speakers",
)

StatusCallback = Callable[[str], None]
ErrorCallback = Callable[[str], None]
LevelCallback = Callable[[float], None]
_LOG_LOCK = threading.Lock()


@dataclass(frozen=True)
class AudioSource:
    mode: str
    name: str
    display_name: str
    token: str
    samplerate: int
    channels: int
    backend: str
    recommended: bool = False


def get_audio_debug_log_path() -> str:
    os.makedirs(DEBUG_DIR, exist_ok=True)
    return DEBUG_LOG_PATH


def clear_audio_debug_log() -> str:
    path = get_audio_debug_log_path()
    with _LOG_LOCK:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"[{datetime.now().isoformat(timespec='seconds')}] audio debug log reset\n")
    return path


def audio_debug_log(message: str) -> str:
    path = get_audio_debug_log_path()
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}\n"
    with _LOG_LOCK:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
    return path


def _hostapi_name(hostapi_index: int) -> str:
    try:
        return str(sd.query_hostapis()[hostapi_index]["name"])
    except Exception:
        return "Unknown"


def looks_virtual_device(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in VIRTUAL_MARKERS)


def looks_system_capture_device(name: str) -> bool:
    lowered = name.lower()
    if looks_virtual_device(name):
        return False
    if "mikrofon" in lowered or "microphone" in lowered or "mic " in lowered:
        return False
    return any(marker in lowered for marker in SYSTEM_CAPTURE_MARKERS)


def _build_input_source(index: int, info: dict, mode: str, recommended: bool = False) -> AudioSource:
    name = str(info["name"])
    backend = _hostapi_name(int(info.get("hostapi", -1)))
    suffix = "  [onerilen]" if recommended else ""
    return AudioSource(
        mode=mode,
        name=name,
        display_name=f"{name} ({backend}){suffix}",
        token=f"sd:{index}",
        samplerate=int(float(info.get("default_samplerate", 48000) or 48000)),
        channels=max(1, int(info.get("max_input_channels", 1))),
        backend=backend,
        recommended=recommended,
    )


def _default_input_index() -> int | None:
    try:
        index = sd.default.device[0]
    except Exception:
        return None
    return int(index) if index is not None and int(index) >= 0 else None


def _backend_rank(name: str) -> int:
    if name == "Windows WASAPI":
        return 0
    if name == "Windows WDM-KS":
        return 1
    if name == "Windows DirectSound":
        return 2
    if name == "MME":
        return 3
    return 9


def list_audio_sources(mode: str) -> list[AudioSource]:
    sources: list[AudioSource] = []

    if mode == MODE_SYSTEM:
        chosen_by_name: dict[str, tuple[int, dict]] = {}
        for index, info in enumerate(sd.query_devices()):
            max_input_channels = int(info.get("max_input_channels", 0))
            if max_input_channels <= 0:
                continue
            name = str(info["name"])
            if not looks_system_capture_device(name):
                continue

            current = chosen_by_name.get(name)
            if current is None:
                chosen_by_name[name] = (index, info)
                continue

            current_backend = _hostapi_name(int(current[1].get("hostapi", -1)))
            new_backend = _hostapi_name(int(info.get("hostapi", -1)))
            if _backend_rank(new_backend) < _backend_rank(current_backend):
                chosen_by_name[name] = (index, info)

        for index, info in chosen_by_name.values():
            name = str(info["name"])
            recommended = "stereo" in name.lower() or "kar" in name.lower()
            backend = _hostapi_name(int(info.get("hostapi", -1)))
            suffix = "  [onerilen]" if recommended else ""
            sources.append(
                AudioSource(
                    mode=MODE_SYSTEM,
                    name=name,
                    display_name=f"{name} ({backend}, Sistem Girisi){suffix}",
                    token=f"sd:{index}",
                    samplerate=int(float(info.get("default_samplerate", 48000) or 48000)),
                    channels=max(1, min(int(info.get("max_input_channels", 1)), 2)),
                    backend=backend,
                    recommended=recommended,
                )
            )

        default_speaker = None
        try:
            default_speaker = sc.default_speaker()
        except Exception:
            default_speaker = None

        for speaker in sc.all_speakers():
            recommended = not sources and bool(default_speaker and speaker.id == default_speaker.id)
            suffix = "  [onerilen]" if recommended else ""
            sources.append(
                AudioSource(
                    mode=MODE_SYSTEM,
                    name=speaker.name,
                    display_name=f"{speaker.name} (Loopback, Yedek){suffix}",
                    token=f"sc:{speaker.id}",
                    samplerate=48000,
                    channels=max(1, min(int(speaker.channels), 2)),
                    backend="SoundCard Loopback",
                    recommended=recommended,
                )
            )
        audio_debug_log(
            f"list_audio_sources mode={mode} count={len(sources)} labels={[source.display_name for source in sources]}"
        )
        return sources

    default_input = _default_input_index()
    chosen_by_name: dict[str, tuple[int, dict]] = {}
    for index, info in enumerate(sd.query_devices()):
        max_input_channels = int(info.get("max_input_channels", 0))
        if max_input_channels <= 0:
            continue

        name = str(info["name"])
        is_virtual = looks_virtual_device(name)
        if mode == MODE_MICROPHONE and is_virtual:
            continue
        if mode == MODE_VIRTUAL and not is_virtual:
            continue

        current = chosen_by_name.get(name)
        if current is None:
            chosen_by_name[name] = (index, info)
            continue

        current_backend = _hostapi_name(int(current[1].get("hostapi", -1)))
        new_backend = _hostapi_name(int(info.get("hostapi", -1)))
        if _backend_rank(new_backend) < _backend_rank(current_backend):
            chosen_by_name[name] = (index, info)

    for index, info in chosen_by_name.values():
        sources.append(
            _build_input_source(
                index=index,
                info=info,
                mode=mode,
                recommended=(index == default_input),
            )
        )

    audio_debug_log(
        f"list_audio_sources mode={mode} count={len(sources)} labels={[source.display_name for source in sources]}"
    )
    return sources


def find_audio_source(sources: list[AudioSource], preferred_value: str) -> AudioSource | None:
    if not sources:
        return None

    if preferred_value:
        for source in sources:
            if preferred_value in {source.token, source.name, source.display_name}:
                return source
        preferred_lower = preferred_value.lower()
        for source in sources:
            if preferred_lower in source.name.lower() or preferred_lower in source.display_name.lower():
                return source

    for source in sources:
        if source.recommended:
            return source
    return sources[0]


def mode_hint(mode: str, has_sources: bool) -> str:
    if has_sources:
        if mode == MODE_SYSTEM:
            return "Sistem sesi icin once Stereo Karisimi veya PC Hoparloru gibi dogrudan girisleri deneyin. Loopback yedek olarak kalir."
        if mode == MODE_VIRTUAL:
            return "Sanal ses aygiti secili. Zoom ve benzeri yonlendirmelerde iyi calisir."
        return "Mikrofon modu secili. Sistem sesi yerine ortam sesi kaydedilir."

    if mode == MODE_SYSTEM:
        return "Sistem sesi kaynagi bulunamadi. Baska bir cikis aygiti secin veya sanal ses aygiti kullanin."
    if mode == MODE_VIRTUAL:
        return "Sanal ses aygiti bulunamadi. HitPaw, VB-Cable veya benzeri bir aygit kurulu olmayabilir."
    return "Kullanilabilir mikrofon bulunamadi."


def compute_level(chunk: np.ndarray) -> float:
    if chunk.size == 0:
        return 0.0
    data = np.asarray(chunk, dtype=np.float32)
    if data.ndim > 1:
        data = data.mean(axis=1)
    peak = float(np.max(np.abs(data)))
    return max(0.0, min(1.0, peak))


def resample_audio(chunk: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    data = np.asarray(chunk, dtype=np.float32)
    if data.ndim > 1:
        data = data.mean(axis=1)
    else:
        data = data.reshape(-1)

    if data.size == 0 or source_rate == target_rate:
        return data

    target_size = max(1, int(round(data.size * (target_rate / float(source_rate)))))
    source_axis = np.linspace(0.0, 1.0, num=data.size, endpoint=False)
    target_axis = np.linspace(0.0, 1.0, num=target_size, endpoint=False)
    return np.interp(target_axis, source_axis, data).astype(np.float32)


def friendly_audio_error(source: AudioSource | None, exc: Exception) -> str:
    raw = str(exc)
    lowered = raw.lower()

    if source and source.mode == MODE_SYSTEM:
        if "blocking api not supported yet" in lowered:
            return "Bu sistem sesi aygiti PortAudio blocking modunu desteklemiyor. Callback tabanli akisa geciliyor veya baska sistem girisi secin."
        if "exclusive" in lowered or "unanticipated host error" in lowered:
            return "Sistem sesi loopback acilamadi. Baska bir cikis aygiti secin veya sanal ses aygiti deneyin."
        return "Sistem sesi kaynagi acilamadi. Hoparlor degistiysa kaynagi yenileyin."

    if "invalid number of channels" in lowered:
        return "Secili aygit bu yakalama bicimini desteklemiyor."
    if "device unavailable" in lowered or "error opening" in lowered:
        return "Secili aygit baska bir uygulama tarafindan kullaniliyor veya erisilemiyor."
    if "permission" in lowered or "access is denied" in lowered:
        return "Windows bu ses aygitina erisim vermedi. Mikrofon ve ses izinlerini kontrol edin."
    return raw or "Bilinmeyen ses aygiti hatasi."


def _effective_channels(source: AudioSource) -> int:
    if source.mode == MODE_SYSTEM and source.token.startswith("sd:"):
        return 1
    return max(1, min(source.channels, 2))


def _resolve_sounddevice_index(source: AudioSource) -> int:
    if not source.token.startswith("sd:"):
        raise ValueError("Sounddevice olmayan kaynak icin sd index istenemez.")

    expected_name = source.name
    expected_backend = source.backend
    try:
        token_index = int(source.token.removeprefix("sd:"))
    except Exception:
        token_index = -1

    devices = list(sd.query_devices())
    if 0 <= token_index < len(devices):
        info = devices[token_index]
        info_name = str(info["name"])
        info_backend = _hostapi_name(int(info.get("hostapi", -1)))
        if info_name == expected_name and info_backend == expected_backend:
            audio_debug_log(
                f"_resolve_sounddevice_index exact-token-match token_index={token_index} name={info_name} backend={info_backend}"
            )
            return token_index

    for index, info in enumerate(devices):
        if int(info.get("max_input_channels", 0)) <= 0:
            continue
        info_name = str(info["name"])
        info_backend = _hostapi_name(int(info.get("hostapi", -1)))
        if info_name == expected_name and info_backend == expected_backend:
            audio_debug_log(
                f"_resolve_sounddevice_index exact-name-backend-match resolved_index={index} name={info_name} backend={info_backend}"
            )
            return index

    for index, info in enumerate(devices):
        if int(info.get("max_input_channels", 0)) <= 0:
            continue
        if str(info["name"]) == expected_name:
            audio_debug_log(
                f"_resolve_sounddevice_index name-only-match resolved_index={index} name={expected_name}"
            )
            return index

    audio_debug_log(
        f"_resolve_sounddevice_index failed token={source.token} expected_name={expected_name} expected_backend={expected_backend}"
    )
    raise ValueError(f"Secili ses kaynagi artik mevcut degil: {expected_name}")


def _ascii_device_hints(source: AudioSource) -> list[str]:
    hints: list[str] = []

    def add(value: str) -> None:
        cleaned = value.strip()
        if cleaned and cleaned not in hints:
            hints.append(cleaned)

    add(source.name)
    inner = re.findall(r"\(([^()]+)\)", source.name)
    for item in inner:
        add(item)

    ascii_name = source.name.encode("ascii", errors="ignore").decode("ascii").strip()
    add(ascii_name)
    for item in inner:
        add(item.encode("ascii", errors="ignore").decode("ascii").strip())

    return hints


def write_audio_debug_snapshot(
    *,
    selected_source: AudioSource | None = None,
    available_sources: list[AudioSource] | None = None,
    note: str = "",
) -> str:
    os.makedirs(DEBUG_DIR, exist_ok=True)
    path = os.path.join(DEBUG_DIR, f"audio-snapshot-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt")
    lines: list[str] = []
    lines.append(f"time={datetime.now().isoformat(timespec='seconds')}")
    if note:
        lines.append(f"note={note}")
    try:
        lines.append(f"default_device={sd.default.device}")
    except Exception as exc:
        lines.append(f"default_device_error={exc!r}")

    lines.append("")
    lines.append("[hostapis]")
    try:
        for index, hostapi in enumerate(sd.query_hostapis()):
            lines.append(f"{index}: {hostapi}")
    except Exception as exc:
        lines.append(f"hostapi_error={exc!r}")

    lines.append("")
    lines.append("[devices]")
    try:
        for index, info in enumerate(sd.query_devices()):
            backend = _hostapi_name(int(info.get("hostapi", -1)))
            lines.append(f"{index}: backend={backend} info={dict(info)}")
    except Exception as exc:
        lines.append(f"device_list_error={exc!r}")

    lines.append("")
    lines.append("[available_sources]")
    for source in available_sources or []:
        lines.append(repr(source))

    lines.append("")
    lines.append("[selected_source]")
    lines.append(repr(selected_source))
    if selected_source is not None and selected_source.token.startswith("sd:"):
        lines.append(f"hints={_ascii_device_hints(selected_source)}")
        try:
            resolved = _resolve_sounddevice_index(selected_source)
            lines.append(f"resolved_index={resolved}")
        except Exception as exc:
            lines.append(f"resolve_error={exc!r}")

    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))

    audio_debug_log(f"write_audio_debug_snapshot path={path} selected={selected_source!r}")
    return path


@contextmanager
def _open_audio_reader(source: AudioSource, frames: int):
    channels = _effective_channels(source)
    audio_debug_log(
        f"_open_audio_reader start source={source!r} frames={frames} channels={channels}"
    )

    if source.token.startswith("sc:"):
        microphone = sc.get_microphone(source.token.removeprefix("sc:"), include_loopback=True)
        with microphone.recorder(
            samplerate=source.samplerate,
            channels=channels,
            blocksize=frames,
        ) as recorder:
            audio_debug_log(f"_open_audio_reader opened soundcard loopback token={source.token}")
            yield lambda: np.asarray(recorder.record(numframes=frames), dtype=np.float32)
        return

    selectors: list[object] = []
    try:
        selectors.append(_resolve_sounddevice_index(source))
    except Exception:
        pass
    selectors.extend(_ascii_device_hints(source))

    last_error: Exception | None = None
    for selector in selectors:
        chunk_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=8)
        audio_debug_log(f"_open_audio_reader trying selector={selector!r} source={source.name}")

        def audio_callback(indata, _frames, _time_info, _status) -> None:
            try:
                chunk_queue.put_nowait(np.asarray(indata.copy(), dtype=np.float32))
            except queue.Full:
                try:
                    chunk_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    chunk_queue.put_nowait(np.asarray(indata.copy(), dtype=np.float32))
                except queue.Full:
                    pass

        try:
            with sd.InputStream(
                device=selector,
                channels=channels,
                samplerate=source.samplerate,
                blocksize=frames,
                dtype="float32",
                latency="low",
                callback=audio_callback,
            ) as stream:
                audio_debug_log(f"_open_audio_reader opened selector={selector!r} stream={stream!r}")
                def read_chunk() -> np.ndarray:
                    try:
                        return chunk_queue.get(timeout=max(0.5, BLOCK_SECONDS * 6))
                    except queue.Empty as exc:
                        audio_debug_log(f"_open_audio_reader timeout selector={selector!r}")
                        raise TimeoutError("Ses verisi zamaninda ulasmadi.") from exc

                yield read_chunk
                return
        except Exception as exc:
            audio_debug_log(f"_open_audio_reader selector_failed selector={selector!r} exc={exc!r}")
            last_error = exc
            continue

    if last_error is not None:
        audio_debug_log(f"_open_audio_reader failed source={source!r} last_error={last_error!r}")
        raise last_error
    audio_debug_log(f"_open_audio_reader failed source={source!r} no-selector-worked")
    raise RuntimeError("Secili ses kaynagi acilamadi.")


def probe_audio_source(source: AudioSource, seconds: float = 0.25) -> tuple[bool, str, float]:
    frames = max(1024, int(source.samplerate * seconds))
    audio_debug_log(
        f"probe_audio_source start source={source!r} seconds={seconds} frames={frames}"
    )
    try:
        with _open_audio_reader(source, frames) as read_chunk:
            chunk = read_chunk()
    except Exception as exc:
        audio_debug_log(f"probe_audio_source error source={source!r} exc={exc!r}")
        return False, friendly_audio_error(source, exc), 0.0

    level = compute_level(chunk)
    audio_debug_log(
        f"probe_audio_source success source={source!r} level={level:.6f} shape={getattr(chunk, 'shape', None)}"
    )
    if level > 0.01:
        return True, "Kaynak acildi ve ses algilandi.", level
    return True, "Kaynak acildi. Su anda sinyal seviyesi dusuk veya sessiz.", level


def capture_audio_sample(
    source: AudioSource,
    seconds: float = 4.0,
    on_level: LevelCallback | None = None,
) -> np.ndarray:
    total_frames = max(1024, int(source.samplerate * max(0.2, seconds)))
    chunk_frames = max(1024, int(source.samplerate * BLOCK_SECONDS))
    collected: list[np.ndarray] = []
    remaining = total_frames
    audio_debug_log(
        f"capture_audio_sample start source={source!r} seconds={seconds} total_frames={total_frames} chunk_frames={chunk_frames}"
    )

    with _open_audio_reader(source, chunk_frames) as read_chunk:
        while remaining > 0:
            chunk = read_chunk()
            if chunk.size == 0:
                break
            collected.append(chunk)
            remaining -= chunk.shape[0]

    if not collected:
        audio_debug_log("capture_audio_sample collected=0")
        return np.zeros((0,), dtype=np.float32)

    chunk = np.concatenate(collected, axis=0)
    audio_debug_log(
        f"capture_audio_sample success collected_chunks={len(collected)} final_shape={chunk.shape} level={compute_level(chunk):.6f}"
    )
    if on_level:
        on_level(compute_level(chunk))
    return np.asarray(chunk, dtype=np.float32)


def to_azure_pcm16(chunk: np.ndarray, source_rate: int) -> np.ndarray:
    resampled = resample_audio(chunk, source_rate, AZURE_SAMPLE_RATE)
    pcm = np.clip(resampled, -1.0, 1.0)
    return (pcm * 32767.0).astype(np.int16)


def write_pcm16_wav(path: str, pcm16: np.ndarray, samplerate: int = AZURE_SAMPLE_RATE) -> None:
    data = np.asarray(pcm16, dtype=np.int16).reshape(-1)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(samplerate))
        handle.writeframes(data.tobytes())


class AudioLevelMonitor(threading.Thread):
    def __init__(
        self,
        *,
        source: AudioSource,
        stop_event: threading.Event,
        on_level: LevelCallback,
        on_status: StatusCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self.source = source
        self.stop_event = stop_event
        self.on_level = on_level
        self.on_status = on_status
        self.on_error = on_error

    def run(self) -> None:
        frames = max(1024, int(self.source.samplerate * BLOCK_SECONDS))
        try:
            with _open_audio_reader(self.source, frames) as read_chunk:
                while not self.stop_event.is_set():
                    chunk = read_chunk()
                    self.on_level(compute_level(chunk))
        except Exception as exc:
            if self.on_error:
                self.on_error(friendly_audio_error(self.source, exc))


class AudioStreamer(threading.Thread):
    def __init__(
        self,
        *,
        source: AudioSource,
        push_stream,
        stop_event: threading.Event,
        on_level: LevelCallback | None = None,
        on_status: StatusCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self.source = source
        self.push_stream = push_stream
        self.stop_event = stop_event
        self.on_level = on_level
        self.on_status = on_status
        self.on_error = on_error

    def run(self) -> None:
        frames = max(1024, int(self.source.samplerate * BLOCK_SECONDS))
        try:
            with _open_audio_reader(self.source, frames) as read_chunk:
                while not self.stop_event.is_set():
                    chunk = read_chunk()
                    level = compute_level(chunk)
                    if self.on_level:
                        self.on_level(level)

                    pcm16 = to_azure_pcm16(chunk, self.source.samplerate)
                    self.push_stream.write(pcm16.tobytes())
        except Exception as exc:  # pragma: no cover - device specific path
            if self.on_error:
                self.on_error(friendly_audio_error(self.source, exc))
