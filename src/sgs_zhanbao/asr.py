"""SenseVoice 本地转写（sherpa-onnx 后端）。

为什么选 SenseVoice 而不是 whisper
-----------------------------------
whisper 是自回归模型：每输出一个 token 都要一次前向，30 秒音频要跑几百次。
SenseVoice 是非自回归：整段音频一次前向出结果，中文专优且自带标点。

实测（i5-13500H，无独显，CPU）::

    whisper tiny   RTF 0.03  术语全错
    whisper base   RTF 0.24  勉强
    whisper small  RTF 0.74  1.5h 要跑 66 分钟
    SenseVoice     RTF≈0.1   中文最好

模型下载
--------
::

    # 需要 sherpa-onnx 的 SenseVoice 模型包（model.int8.onnx + tokens.txt）
    # 官方地址：https://github.com/k2-fsa/sherpa-onnx/releases
    curl -L -o sensevoice.tar.bz2 \
      https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2
    tar -xjf sensevoice.tar.bz2 -C models/

目录可用 ``SGS_ASR_MODELS`` 指到别处（默认 ``./models``）。
"""

from __future__ import annotations

import logging
import time
import wave
from pathlib import Path
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000


def models_root() -> Path:
    import os

    return Path(os.environ.get("SGS_ASR_MODELS") or Path.cwd() / "models").expanduser()


def _find_model(d: Path) -> Path:
    """模型解包后可能多一层目录，这里自动找 model.int8.onnx 所在目录。"""
    if not d.exists():
        raise FileNotFoundError(
            f"模型目录不存在：{d}\n"
            f"按 docs/本地转写.md 下载 SenseVoice 模型，或用 SGS_ASR_MODELS 指定目录"
        )
    for cand in [d, *[p for p in d.iterdir() if p.is_dir()]]:
        if (cand / "model.int8.onnx").exists() or (cand / "model.onnx").exists():
            return cand
    raise FileNotFoundError(f"在 {d} 下找不到 model.int8.onnx / model.onnx")


def load_wav(path: str | Path) -> np.ndarray:
    """读 WAV → float32 单声道 16k。"""
    with wave.open(str(path), "rb") as w:
        n_ch = w.getnchannels()
        rate = w.getframerate()
        width = w.getsampwidth()
        frames = w.readframes(w.getnframes())

    if width != 2:
        raise ValueError(f"只支持 PCM16 WAV，当前 {width * 8} bit（先用 ffmpeg 转码）")

    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if n_ch > 1:
        audio = audio.reshape(-1, n_ch).mean(axis=1)
    if rate != SAMPLE_RATE:
        n = int(round(audio.size * SAMPLE_RATE / rate))
        audio = np.interp(
            np.linspace(0, audio.size, n, endpoint=False), np.arange(audio.size), audio
        ).astype(np.float32)
    return audio


class SenseVoice:
    """封装 sherpa-onnx 的 SenseVoice 识别器。"""

    def __init__(self, model_dir: str | Path | None = None, *, num_threads: int = 4) -> None:
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise RuntimeError(
                "本地转写需要 sherpa-onnx：pip install 'sgs-zhanbao[asr]'"
            ) from exc

        self.model_dir = _find_model(Path(model_dir) if model_dir else models_root())
        model = self.model_dir / "model.int8.onnx"
        if not model.exists():
            model = self.model_dir / "model.onnx"
        tokens = self.model_dir / "tokens.txt"
        if not tokens.exists():
            raise FileNotFoundError(f"缺少 tokens.txt：{self.model_dir}")

        logger.info("加载 SenseVoice：%s", model.name)
        t0 = time.time()
        self.rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model),
            tokens=str(tokens),
            num_threads=num_threads,
            use_itn=True,          # 逆文本归一化：数字 / 标点
            debug=False,
        )
        logger.info("加载完成 %.2fs", time.time() - t0)

    def transcribe_array(self, audio: np.ndarray) -> str:
        stream = self.rec.create_stream()
        stream.accept_waveform(SAMPLE_RATE, audio)
        self.rec.decode_stream(stream)
        return stream.result.text.strip()

    def transcribe_file(self, path: str | Path) -> tuple[str, float, float]:
        """返回 (文本, 音频秒数, 耗时秒数)。"""
        audio = load_wav(path)
        t0 = time.time()
        text = self.transcribe_array(audio)
        return text, audio.size / SAMPLE_RATE, time.time() - t0


# ---------------------------------------------------------------- 长音频切分

def split_audio(
    audio: np.ndarray, chunk_s: float = 30.0, overlap_s: float = 1.0
) -> list[tuple[int, np.ndarray]]:
    """按固定长度切块（带重叠，避免切断句子）。SenseVoice 官方建议单段 ≤30 秒。"""
    n = int(chunk_s * SAMPLE_RATE)
    step = max(1, int((chunk_s - overlap_s) * SAMPLE_RATE))
    out: list[tuple[int, np.ndarray]] = []
    i = 0
    while i < audio.size:
        out.append((i, audio[i : i + n]))
        if i + n >= audio.size:
            break
        i += step
    return out


def _stamp(sample_index: int) -> str:
    ts = int(sample_index / SAMPLE_RATE)
    return f"{ts // 3600:02d}:{(ts % 3600) // 60:02d}:{ts % 60:02d}"


def transcribe_long(
    rec: SenseVoice,
    wav_path: str | Path,
    *,
    chunk_s: float = 30.0,
    log: Callable[[str], None] = print,
) -> dict:
    """长音频转写：切块识别后拼接，带时间戳（输出格式与 ingest 兼容）。"""
    audio = load_wav(wav_path)
    total_s = audio.size / SAMPLE_RATE
    chunks = split_audio(audio, chunk_s=chunk_s)
    log(f"音频 {total_s / 60:.1f} 分钟，切成 {len(chunks)} 块")

    t0 = time.time()
    lines: list[str] = []
    for idx, (start, seg) in enumerate(chunks, 1):
        text = rec.transcribe_array(seg)
        if text:
            lines.append(f"[{_stamp(start)}] {text}")
        if idx % 10 == 0 or idx == len(chunks):
            elapsed = time.time() - t0
            done_s = min(total_s, idx * chunk_s)
            rtf = elapsed / max(0.1, done_s)
            log(f"  {idx}/{len(chunks)}  已用 {elapsed:.0f}s  RTF≈{rtf:.3f}")

    elapsed = time.time() - t0
    return {
        "text": "\n".join(lines),
        "audio_seconds": total_s,
        "elapsed_seconds": elapsed,
        "rtf": elapsed / max(0.1, total_s),
        "chunks": len(chunks),
    }
