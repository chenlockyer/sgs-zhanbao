"""流式语音识别：边录边转，直播结束即出稿。

为什么用流式
------------
离线模型（whisper / SenseVoice）必须等整段音频录完才开始转，1.5 小时直播要再等
20-70 分钟。流式模型在直播进行时就把话说完了，赛事一结束稿子已经在手里。

模型选型
--------
sherpa-onnx 的 streaming-paraformer（达摩院 Paraformer 流式版）：

* 中文专优，支持普通话 + 方言 + 英文混说（解说常有英文）
* int8 量化后体积小，CPU 上 RTF≈0.15，远快于实时
* 缺点：不带标点，需配标点模型；不提供精确时间戳

架构::

    WASAPI loopback → 环形缓冲 → 分段喂给 OnlineRecognizer
                                         ↓
                            识别结果落在临时文本，按 endpoint 切句
                                         ↓
                    每句补标点 → 追加写 txt（崩溃也不丢）

模型目录用 ``SGS_ASR_MODELS`` 指定（默认 ``./models``）。
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
DEFAULT_MODEL = "sherpa-onnx-streaming-paraformer-bilingual-zh-en"
DEFAULT_PUNCT = "sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12"


def models_root() -> Path:
    return Path(os.environ.get("SGS_ASR_MODELS") or Path.cwd() / "models").expanduser()


def _find_dir(name: str, root: Path | None = None) -> Path:
    """在 models/ 下找解包后的模型目录。"""
    base = root or models_root()
    for p in sorted(base.glob(f"{name}*")):
        if p.is_dir():
            return p
    raise FileNotFoundError(
        f"找不到模型目录 {name}*，请先下载解包到 {base}\n"
        f"见 docs/本地转写.md"
    )


@dataclass
class OnlineResult:
    text: str
    is_final: bool
    timestamp: float = 0.0


class StreamingRecognizer:
    """把流式识别封装成「喂音频 → 出文本」的简单接口。"""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        num_threads: int = 4,
        punct_model: str | None = DEFAULT_PUNCT,
        on_result: Callable[[OnlineResult], None] | None = None,
        models_dir: str | Path | None = None,
    ) -> None:
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise RuntimeError(
                "流式转写需要 sherpa-onnx：pip install 'sgs-zhanbao[asr]'"
            ) from exc

        root = Path(models_dir) if models_dir else None
        d = _find_dir(model_name, root)
        enc, dec = d / "encoder.int8.onnx", d / "decoder.int8.onnx"
        if not enc.exists():
            enc, dec = d / "encoder.onnx", d / "decoder.onnx"
        tokens = d / "tokens.txt"
        for f in (enc, dec, tokens):
            if not f.exists():
                raise FileNotFoundError(f"缺少模型文件：{f}")

        logger.info("加载流式模型 %s", d.name)
        t0 = time.time()
        self.rec = sherpa_onnx.OnlineRecognizer.from_paraformer(
            tokens=str(tokens),
            encoder=str(enc),
            decoder=str(dec),
            num_threads=num_threads,
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            decoding_method="greedy_search",
            enable_endpoint_detection=True,
            rule1_min_trailing_silence=2.4,
            rule2_min_trailing_silence=1.2,
            rule3_min_utterance_length=20.0,
        )
        self.stream = self.rec.create_stream()
        logger.info("模型就绪 %.2fs", time.time() - t0)

        self.on_result = on_result
        self._last_partial = ""
        self._samples_fed = 0

        self.punct = None
        if punct_model:
            try:
                pd = _find_dir(punct_model, root)
                model = pd / "model.int8.onnx"
                if not model.exists():
                    model = pd / "model.onnx"
                self.punct = sherpa_onnx.OfflinePunctuation(model=str(model), num_threads=2)
                logger.info("标点模型已加载")
            except Exception as exc:  # noqa: BLE001
                logger.warning("标点模型不可用（将输出无标点文本）：%s", exc)

    # ---------------------------------------------------------- 喂音频

    def accept(self, audio: np.ndarray) -> list[OnlineResult]:
        """喂一段 float32 mono 16k 音频，返回本次产生的所有结果。"""
        if audio.size == 0:
            return []
        self.stream.accept_waveform(SAMPLE_RATE, audio.astype(np.float32))
        self._samples_fed += audio.size

        results: list[OnlineResult] = []
        while self.rec.is_ready(self.stream):
            self.rec.decode_stream(self.stream)
            txt = self.rec.get_result(self.stream).strip()
            if txt and txt != self._last_partial:
                self._last_partial = txt
                r = OnlineResult(text=txt, is_final=False, timestamp=self.elapsed)
                results.append(r)
                if self.on_result:
                    self.on_result(r)

            if self.rec.is_endpoint(self.stream):
                final_txt = self.rec.get_result(self.stream).strip()
                self.rec.reset(self.stream)
                self._last_partial = ""
                if final_txt:
                    r = OnlineResult(
                        text=self.add_punct(final_txt), is_final=True, timestamp=self.elapsed
                    )
                    results.append(r)
                    if self.on_result:
                        self.on_result(r)
        return results

    def flush(self) -> OnlineResult | None:
        """收尾：把缓冲区里最后一句吐出来。"""
        self.stream.accept_waveform(
            SAMPLE_RATE, np.zeros(int(0.6 * SAMPLE_RATE), dtype=np.float32)
        )
        self.stream.input_finished()
        while self.rec.is_ready(self.stream):
            self.rec.decode_stream(self.stream)
        txt = self.rec.get_result(self.stream).strip()
        if not txt:
            return None
        r = OnlineResult(text=self.add_punct(txt), is_final=True, timestamp=self.elapsed)
        if self.on_result:
            self.on_result(r)
        return r

    # ---------------------------------------------------------- 辅助

    def add_punct(self, text: str) -> str:
        if not text or self.punct is None:
            return text
        try:
            return self.punct.add_punctuation(text)
        except Exception:  # noqa: BLE001
            return text

    @property
    def elapsed(self) -> float:
        return self._samples_fed / SAMPLE_RATE


class StreamingTranscriber:
    """串起「录音 + 流式识别 + 落盘」的完整流程。"""

    def __init__(
        self,
        out_path: str | Path,
        *,
        model_name: str = DEFAULT_MODEL,
        device_index: int | None = None,
        num_threads: int = 4,
        flush_seconds: float = 4.0,
        log: Callable[[str], None] = print,
    ) -> None:
        self.out_path = Path(out_path)
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.device_index = device_index
        self.flush_seconds = flush_seconds
        self.log = log

        self._stop = False
        self._lines: list[str] = []
        self._final_count = 0
        self.rec = StreamingRecognizer(
            model_name, num_threads=num_threads, on_result=self._on_result
        )
        self._thread: _CaptureThread | None = None

    # ---------------------------------------------------------- 回调

    def _on_result(self, r: OnlineResult) -> None:
        if not r.is_final or not r.text.strip():
            return
        self._final_count += 1
        line = f"[{_stamp(r.timestamp)}] {r.text.strip()}"
        self._lines.append(line)
        with self.out_path.open("a", encoding="utf-8") as f:   # 实时落盘，崩溃不丢
            f.write(line + "\n")

    # ---------------------------------------------------------- 控制

    def start(self) -> None:
        self.out_path.write_text("", encoding="utf-8")
        self._stop = False
        self._thread = _CaptureThread(self)
        self._thread.start()
        logger.info("流式转写已启动 → %s", self.out_path)

    def stop(self) -> Path:
        self._stop = True
        if self._thread:
            self._thread.join(timeout=5.0)
        self.rec.flush()
        logger.info("流式转写结束，共 %d 句", self._final_count)
        return self.out_path

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def lines(self) -> list[str]:
        return list(self._lines)

    @property
    def audio_seconds(self) -> float:
        return self.rec.elapsed


def _stamp(seconds: float) -> str:
    ts = int(seconds)
    return f"{ts // 3600:02d}:{(ts % 3600) // 60:02d}:{ts % 60:02d}"


class _CaptureThread:
    """WASAPI 采集线程，把音频喂给识别器。"""

    def __init__(self, owner: StreamingTranscriber) -> None:
        import threading

        self.owner = owner
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float) -> None:
        self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def _run(self) -> None:
        try:
            import pyaudiowpatch as pyaudio
        except ImportError:
            logger.error("缺 PyAudioWPatch：pip install 'sgs-zhanbao[asr]'")
            return

        from .record import _pick_loopback, _resample

        o = self.owner
        pa = pyaudio.PyAudio()
        try:
            idx = o.device_index if o.device_index is not None else _pick_loopback(pa)
            dev = pa.get_device_info_by_index(idx)
            ch = int(dev.get("maxInputChannels", 1)) or 1
            rate = int(float(dev.get("defaultSampleRate", 48000)))
            logger.info("采集设备 [%d] %s  %dHz %dch", idx, dev.get("name"), rate, ch)

            def cb(in_data, frame_count, time_info, status):  # noqa: ARG001
                if o._stop:
                    return (None, pyaudio.paComplete)
                a = np.frombuffer(in_data, dtype=np.float32)
                if ch > 1:
                    a = a.reshape(-1, ch).mean(axis=1)
                a = _resample(a, rate, SAMPLE_RATE)
                if a.size:
                    try:
                        o.rec.accept(a)
                    except Exception:  # noqa: BLE001
                        logger.exception("识别失败")
                return (None, pyaudio.paContinue)

            stream = pa.open(
                format=pyaudio.paFloat32, channels=ch, rate=rate,
                input=True, input_device_index=idx,
                frames_per_buffer=int(0.3 * rate), stream_callback=cb,
            )
            stream.start_stream()
            while not o._stop:
                time.sleep(0.1)
                if not stream.is_active():
                    break
            stream.stop_stream()
            stream.close()
        except Exception:  # noqa: BLE001
            logger.exception("流式采集失败")
        finally:
            pa.terminate()


def merge_lines_to_paragraphs(lines: list[str], max_chars: int = 220) -> str:
    """把逐句结果合并成自然段落，给下游 LLM 更好读。"""
    out: list[str] = []
    buf: list[str] = []
    size = 0
    stamp = ""

    for line in lines:
        m = re.match(r"\[(\d{2}:\d{2}:\d{2})\]\s*(.*)", line)
        if not m:
            continue
        ts, txt = m.group(1), m.group(2).strip()
        if not stamp:
            stamp = ts
        if size + len(txt) > max_chars and buf:
            out.append(f"{stamp}  {' '.join(buf)}")
            buf, size, stamp = [], 0, ts
        buf.append(txt)
        size += len(txt)

    if buf:
        out.append(f"{stamp}  {' '.join(buf)}")
    return "\n\n".join(out)
