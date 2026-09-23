from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class LoopbackPacket:
    samples: Any
    level_db: float
    timestamp: float


class LoopbackSubscription:
    def __init__(self, broker: "SharedLoopbackBroker", key: str, subscriber_id: str, state: dict[str, Any], packet_queue: queue.Queue):
        self._broker = broker
        self._key = key
        self._subscriber_id = subscriber_id
        self._state = state
        self._queue = packet_queue
        self.samplerate = int(state["samplerate"])
        self.channels = int(state["channels"])
        self.device_name = str(state.get("device_name", "Speaker output"))
        self._closed = False

    def read(self, timeout: float = 1.0) -> LoopbackPacket:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty as exc:
            if not self._state["thread"].is_alive():
                message = str(self._state.get("error") or "The shared speaker capture stopped unexpectedly.")
                raise RuntimeError(message) from exc
            raise

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._broker.unsubscribe(self._key, self._subscriber_id)


class SharedLoopbackBroker:
    """Open each Windows speaker loopback once and fan its frames out to many VIC jobs."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._captures: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _key(speaker_id: str, speaker_name: str) -> str:
        return (speaker_id or speaker_name or "default-speaker").strip().casefold()

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        text = str(exc)
        if "0x8889000a" in text.lower():
            return (
                "Windows refused the first shared loopback connection (0x8889000A). "
                "VIC now reuses one connection internally, so this usually means another non-VIC program "
                "has the selected output in exclusive mode. Disable exclusive mode for that playback device "
                "or close the program holding it, then retry."
            )
        return text

    def subscribe(self, subscriber_id: str, speaker_id: str, speaker_name: str, samplerate: int = 48000) -> LoopbackSubscription:
        key = self._key(speaker_id, speaker_name)
        packet_queue: queue.Queue = queue.Queue(maxsize=24)
        created = False

        with self._lock:
            state = self._captures.get(key)
            if state is None or state["stop"].is_set() or not state["thread"].is_alive():
                ready = threading.Event()
                stop = threading.Event()
                state = {
                    "key": key,
                    "speaker_id": speaker_id,
                    "speaker_name": speaker_name,
                    "requested_rate": int(samplerate or 48000),
                    "samplerate": int(samplerate or 48000),
                    "channels": 2,
                    "device_name": speaker_name or speaker_id or "Default speaker",
                    "subscribers": {},
                    "ready": ready,
                    "stop": stop,
                    "error": "",
                    "thread": None,
                    "latest_db": -70.0,
                }
                thread = threading.Thread(
                    target=self._capture_loop,
                    args=(state,),
                    daemon=True,
                    name=f"VIC-SharedLoopback-{abs(hash(key)) & 0xFFFF:04x}",
                )
                state["thread"] = thread
                self._captures[key] = state
                created = True
            state["subscribers"][subscriber_id] = packet_queue
            thread = state["thread"]

        if created:
            thread.start()

        if not state["ready"].wait(timeout=12):
            self.unsubscribe(key, subscriber_id)
            raise RuntimeError("Timed out while opening the shared Windows speaker loopback.")
        if state.get("error"):
            error = str(state["error"])
            self.unsubscribe(key, subscriber_id)
            raise RuntimeError(error)

        return LoopbackSubscription(self, key, subscriber_id, state, packet_queue)

    def unsubscribe(self, key: str, subscriber_id: str) -> None:
        with self._lock:
            state = self._captures.get(key)
            if not state:
                return
            state["subscribers"].pop(subscriber_id, None)
            if not state["subscribers"]:
                state["stop"].set()

    def _capture_loop(self, state: dict[str, Any]) -> None:
        try:
            import numpy as np
            import soundcard as sc

            speaker_id = str(state.get("speaker_id", ""))
            speaker_name = str(state.get("speaker_name", ""))
            loopback = None
            resolution_errors: list[str] = []
            for identifier in [speaker_id, speaker_name]:
                if not identifier:
                    continue
                try:
                    loopback = sc.get_microphone(id=identifier, include_loopback=True)
                    break
                except Exception as exc:
                    resolution_errors.append(str(exc))
            if loopback is None and not speaker_id and not speaker_name:
                default = sc.default_speaker()
                if default is not None:
                    loopback = sc.get_microphone(id=getattr(default, "id", ""), include_loopback=True)
            if loopback is None:
                raise RuntimeError("Could not resolve the selected speaker output. " + " | ".join(resolution_errors[-2:]))

            state["device_name"] = str(getattr(loopback, "name", "") or speaker_name or speaker_id or "Speaker output")
            raw_channels = getattr(loopback, "channels", 2) or 2
            channels = len(raw_channels) if isinstance(raw_channels, (list, tuple)) else int(raw_channels)
            channels = max(1, min(2, channels))

            requested = int(state.get("requested_rate", 48000) or 48000)
            rates: list[int] = []
            for rate in [requested, 48000, 44100]:
                if rate not in rates:
                    rates.append(rate)

            last_error: Exception | None = None
            for rate in rates:
                try:
                    with loopback.recorder(samplerate=rate, channels=channels, blocksize=2048) as recorder:
                        state["samplerate"] = rate
                        state["channels"] = channels
                        state["error"] = ""
                        state["ready"].set()
                        while not state["stop"].is_set():
                            frames = recorder.record(numframes=2048)
                            samples = np.asarray(frames, dtype=np.float32)
                            if samples.ndim == 1:
                                samples = samples.reshape(-1, 1)
                            samples = np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0)
                            rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
                            level_db = max(-70.0, min(0.0, 20.0 * float(np.log10(max(rms, 1e-7)))))
                            packet = LoopbackPacket(samples=samples, level_db=round(level_db, 1), timestamp=time.time())
                            state["latest_db"] = packet.level_db
                            with self._lock:
                                subscribers = list(state["subscribers"].values())
                            for subscriber_queue in subscribers:
                                try:
                                    subscriber_queue.put_nowait(packet)
                                except queue.Full:
                                    try:
                                        subscriber_queue.get_nowait()
                                    except queue.Empty:
                                        pass
                                    try:
                                        subscriber_queue.put_nowait(packet)
                                    except queue.Full:
                                        pass
                        return
                except Exception as exc:
                    last_error = exc
                    if state["ready"].is_set():
                        break

            raise RuntimeError(self._friendly_error(last_error or RuntimeError("Unknown loopback error")))
        except Exception as exc:
            state["error"] = self._friendly_error(exc)
            state["ready"].set()
        finally:
            state["stop"].set()
            with self._lock:
                current = self._captures.get(state["key"])
                if current is state:
                    self._captures.pop(state["key"], None)
