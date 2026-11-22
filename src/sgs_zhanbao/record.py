"""整场录音：捕获系统声音，直接写 WAV，供后续离线转写。

与流式转写（:mod:`sgs_zhanbao.stream_asr`）不同，这里只负责「无损落盘」：

* 边录边写，中途崩溃也不丢前面的内容
* 固定 16kHz 单声道 PCM16（SenseVoice / whisper 都吃这个格式）
* 支持按最长时长自动停，也能 Ctrl+C 停

为什么单独做：实时转写要同时做采集 + 推理，风扇吵、CPU 打满，影响你看直播。
先录后转，体验好很多。

依赖 Windows WASAPI 环回（``pip install 'sgs-zhanbao[asr]'``），
非 Windows 平台需要外部回环设备（如 BlackHole / PulseAudio monitor）。
"""

from __future__ import annotations

import logging
import threading
import time
import wave
from collections import deque
from pathlib import Path
from typing import Any, Callable

import numpy as np

logger = logging.getLogger(__name__)

TARGET_RATE = 16000


class Recorder:
    """把 WASAPI loopback 音频连续写进 WAV 文件。"""

    def __init__(
        self,
        out_path: str | Path,
        *,
        sample_rate: int = TARGET_RATE,
        device_index: int | None = None,
        flush_seconds: float = 5.0,
    ) -> None:
        self.out_path = Path(out_path)
        self.sample_rate = sample_rate
        self.device_index = device_index
        self.flush_seconds = flush_seconds

        self._buf: deque[np.ndarray] = deque()
        self._buf_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._writer_thread: threading.Thread | None = None
        self._wf: Any = None
        self._frames_written = 0
        self._error: BaseException | None = None

        self._device_rate = sample_rate
        self._device_channels = 1
        self._peak = 0.0
        self._started_at = 0.0

    # ---------------------------------------------------------- 状态

    @property
    def is_recording(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def seconds_recorded(self) -> float:
        return self._frames_written / float(self.sample_rate)

    @property
    def elapsed(self) -> float:
        return time.time() - self._started_at if self._started_at else 0.0

    @property
    def peak(self) -> float:
        """采集到的最大振幅（0~1）；跑完仍是 0 说明根本没声音。"""
        return self._peak

    @property
    def error(self) -> BaseException | None:
        return self._error

    # ---------------------------------------------------------- 控制

    def start(self) -> None:
        if self.is_recording:
            return
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        # 故意不 with：wave 要跨整个录音期间保持打开，由 stop() 负责关闭
        self._wf = wave.open(str(self.out_path), "wb")  # noqa: SIM115
        self._wf.setnchannels(1)
        self._wf.setsampwidth(2)          # PCM16
        self._wf.setframerate(self.sample_rate)

        self._stop.clear()
        self._started_at = time.time()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._writer_thread.start()
        logger.info("开始录音 → %s", self.out_path)

    def stop(self) -> Path:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        if self._writer_thread:
            self._writer_thread.join(timeout=5.0)
        self._drain()
        if self._wf:
            try:
                self._wf.close()
            finally:
                self._wf = None
        logger.info(
            "录音结束：%.1fs，%d 帧 → %s",
            self.seconds_recorded, self._frames_written, self.out_path,
        )
        return self.out_path

    # ---------------------------------------------------------- 内部

    def _capture_loop(self) -> None:
        try:
            import pyaudiowpatch as pyaudio
        except ImportError as exc:  # pragma: no cover
            self._error = exc
            logger.error("缺 PyAudioWPatch：pip install 'sgs-zhanbao[asr]'")
            return

        pa = pyaudio.PyAudio()
        try:
            idx = self.device_index if self.device_index is not None else _pick_loopback(pa)
            dev = pa.get_device_info_by_index(idx)
            self._device_channels = int(dev.get("maxInputChannels", 1)) or 1
            self._device_rate = int(float(dev.get("defaultSampleRate", 48000)))
            logger.info(
                "设备 [%d] %s  设备率=%d 通道=%d",
                idx, dev.get("name"), self._device_rate, self._device_channels,
            )

            def cb(in_data, frame_count, time_info, status):  # noqa: ARG001
                if self._stop.is_set():
                    return (None, pyaudio.paComplete)
                audio = np.frombuffer(in_data, dtype=np.float32)
                if self._device_channels > 1:
                    audio = audio.reshape(-1, self._device_channels).mean(axis=1)
                audio = _resample(audio, self._device_rate, self.sample_rate)
                if audio.size:
                    self._peak = max(self._peak, float(np.abs(audio).max()))
                    with self._buf_lock:
                        self._buf.append(audio.astype(np.float32, copy=False))
                return (None, pyaudio.paContinue)

            stream = pa.open(
                format=pyaudio.paFloat32,
                channels=self._device_channels,
                rate=self._device_rate,
                input=True,
                input_device_index=idx,
                frames_per_buffer=int(0.5 * self._device_rate),
                stream_callback=cb,
            )
            stream.start_stream()
            while not self._stop.is_set():
                time.sleep(0.1)
                if not stream.is_active():
                    logger.warning("采集流意外停止")
                    break
            stream.stop_stream()
            stream.close()
        except Exception as exc:  # noqa: BLE001
            self._error = exc
            logger.exception("采集失败：%s", exc)
        finally:
            pa.terminate()

    def _writer_loop(self) -> None:
        """独立线程定期把缓冲写成 PCM16，保证中途崩溃也有内容。"""
        while not self._stop.is_set():
            time.sleep(0.5)
            self._drain()
        self._drain()

    def _drain(self) -> None:
        with self._buf_lock:
            if not self._buf:
                return
            chunks = list(self._buf)
            self._buf.clear()
        audio = np.concatenate(chunks)
        pcm = np.clip(audio, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype(np.int16)
        if self._wf is not None:
            self._wf.writeframes(pcm.tobytes())
            self._frames_written += pcm.size


# ---------------------------------------------------------------- 工具

def _resample(audio: np.ndarray, src: int, dst: int) -> np.ndarray:
    """线性重采样（够用；不引入 librosa / soxr 这类重依赖）。"""
    if not audio.size or src == dst:
        return audio
    n = int(round(audio.size * dst / src))
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    return np.interp(
        np.linspace(0, audio.size, n, endpoint=False), np.arange(audio.size), audio
    ).astype(np.float32)


def _pick_loopback(pa) -> int:
    """优先选与默认输出设备同名的 loopback，其次任意 loopback。"""
    try:
        import pyaudiowpatch as pyaudio

        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_out = wasapi.get("defaultOutputDevice")
        if default_out is not None:
            out_name = pa.get_device_info_by_index(default_out).get("name", "")
            for i in range(pa.get_device_count()):
                d = pa.get_device_info_by_index(i)
                if d.get("isLoopbackDevice") and out_name and out_name in d.get("name", ""):
                    logger.info("选中默认输出对应的 loopback：%s", d.get("name"))
                    return i
    except Exception:  # noqa: BLE001
        pass

    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if d.get("isLoopbackDevice"):
            logger.info("回退到第一个 loopback：%s", d.get("name"))
            return i
    raise RuntimeError(
        "找不到 WASAPI loopback 设备（需要 Windows；"
        "其它平台请用 BlackHole / PulseAudio monitor 等虚拟回环）"
    )


def list_devices() -> list[dict]:
    """列出所有回环设备，方便 ``--device`` 指定。"""
    import pyaudiowpatch as pyaudio

    pa = pyaudio.PyAudio()
    try:
        out = []
        for i in range(pa.get_device_count()):
            d = pa.get_device_info_by_index(i)
            if d.get("isLoopbackDevice"):
                out.append(
                    {
                        "index": i,
                        "name": d.get("name"),
                        "rate": int(float(d.get("defaultSampleRate", 0))),
                        "channels": int(d.get("maxInputChannels", 0)),
                    }
                )
        return out
    finally:
        pa.terminate()


def record(out: str | Path, *, duration: float | None = None, device: int | None = None,
           log: Callable[[str], None] = print) -> Path:
    """把系统声音录成 WAV。``duration=None`` 时 Ctrl+C 停止。"""
    rec = Recorder(out, device_index=device)
    rec.start()
    log(f"录音中 → {rec.out_path}   （Ctrl+C 停止）")
    try:
        while rec.is_recording:
            time.sleep(1)
            log(
                f"\r  {rec.seconds_recorded:7.1f}s  峰值 {rec.peak:.3f}"
                + (f" / {duration:.0f}s" if duration else "")
            )
            if duration and rec.elapsed >= duration:
                log("\n达到设定时长")
                break
    except KeyboardInterrupt:
        log("\n停止中…")
    finally:
        path = rec.stop()

    if rec.peak < 0.001:
        log("\n⚠ 峰值极低，可能没声音：检查系统输出设备 / 是否选中了正确的 loopback")
    if rec.error:
        raise RuntimeError(f"录音失败：{rec.error}")
    size_mb = path.stat().st_size / 1024 / 1024
    log(f"✅ {path}  {size_mb:.1f} MB  {rec.seconds_recorded / 60:.1f} 分钟")
    return path


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="录制系统声音到 WAV")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("devices", help="列出可用的回环设备")
    pr = sub.add_parser("record", help="开始录音")
    pr.add_argument("--out", required=True)
    pr.add_argument("--duration", type=float, default=None, help="录多少秒后自动停")
    pr.add_argument("--device", type=int, default=None)
    a = ap.parse_args(argv)

    if a.cmd == "devices":
        for d in list_devices():
            print(f"[{d['index']}] {d['name']}  {d['rate']}Hz {d['channels']}ch")
        return 0
    if a.cmd == "record":
        record(a.out, duration=a.duration, device=a.device)
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
