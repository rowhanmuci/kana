"""
audio.py — YouTube 音訊分析

下載 YouTube 音訊並執行多階段分析：
  1. librosa CPU 分析（tempo、調性、音域、能量分佈）
  2. Demucs v4 (htdemucs) GPU 音軌分離 → 鼓組/貝斯特性分析
  3. Whisper large-v3 GPU 語音轉錄

任何階段失敗都不中斷整體，回傳已取得的部分結果。
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


async def analyze_youtube_audio(video_id: str) -> dict:
    """
    分析指定 YouTube 影片的音訊。

    Returns:
        dict，可能包含：tempo, key, vocal_range, energy_desc, peak_str,
        duration_str, drum_char, bass_root, transcript, segments
        任何欄位都可能缺失（分析失敗時）。
    """
    import os
    import tempfile
    import imageio_ffmpeg
    import numpy as np

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    ffmpeg_dir = os.path.dirname(ffmpeg_exe)
    if ffmpeg_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

    loop = asyncio.get_event_loop()
    yt_url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # ── 1. yt-dlp 下載 ───────────────────────────────────────────
            import glob as _glob
            import yt_dlp
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": os.path.join(tmpdir, "audio.%(ext)s"),
                "ffmpeg_location": ffmpeg_dir,
                "quiet": True,
                "no_warnings": True,
            }
            logger.info("[audio] 下載：%s", video_id)
            await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).download([yt_url]))
            found = _glob.glob(os.path.join(tmpdir, "audio.*"))
            if not found:
                logger.warning("[audio] 音訊下載失敗，跳過")
                return {}
            audio_path = found[0]
            logger.info("[audio] 下載完成：%s", os.path.basename(audio_path))

            # 轉成 44100Hz stereo wav
            import subprocess
            wav_path = os.path.join(tmpdir, "audio.wav")
            conv = subprocess.run(
                [ffmpeg_exe, "-i", audio_path, "-ar", "44100", "-ac", "2", wav_path, "-y"],
                capture_output=True,
            )
            if conv.returncode != 0 or not os.path.exists(wav_path):
                logger.warning("[audio] wav 轉換失敗：%s", conv.stderr.decode(errors="replace")[:200])
                return {}
            logger.info("[audio] wav 轉換完成")

            # ── 2. librosa CPU 分析 ──────────────────────────────────────
            import librosa

            _KEY_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
            _MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
            _MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

            def _librosa_analysis():
                y, sr = librosa.load(wav_path, sr=22050, mono=True)

                tempo_arr, _ = librosa.beat.beat_track(y=y, sr=sr)
                tempo_val = float(np.atleast_1d(tempo_arr)[0])
                hop = int(sr * 0.5)
                rms = librosa.feature.rms(y=y, hop_length=hop)[0]
                rms_norm = rms / (rms.max() + 1e-9)
                peak_sec = float(rms_norm.argmax()) * 0.5
                n = len(rms_norm)
                def _lbl(arr):
                    v = float(arr.mean())
                    return "強" if v > 0.65 else ("中" if v > 0.35 else "弱")
                energy_desc = "、".join(
                    f"{p}{_lbl(rms_norm[n*i//3:n*(i+1)//3])}"
                    for i, p in enumerate(["前段", "中段", "後段"])
                )
                dur = len(y) / sr

                chroma_mean = librosa.feature.chroma_stft(y=y, sr=sr).mean(axis=1)
                best_corr, best_key = -np.inf, "unknown"
                for i in range(12):
                    for mode, prof in [("major", _MAJOR_PROFILE), ("minor", _MINOR_PROFILE)]:
                        corr = float(np.corrcoef(chroma_mean, np.roll(prof, i))[0, 1])
                        if corr > best_corr:
                            best_corr, best_key = corr, f"{_KEY_NAMES[i]} {mode}"

                y_harm, _ = librosa.effects.hpss(y)
                f0, _, _ = librosa.pyin(y_harm, fmin=150, fmax=900, sr=sr)
                valid_f0 = f0[~np.isnan(f0)] if f0 is not None else np.array([])
                vocal_range = None
                if len(valid_f0) > 20:
                    low_p, high_p = np.percentile(valid_f0, [5, 95])
                    vocal_range = {
                        "low": librosa.hz_to_note(float(low_p)),
                        "high": librosa.hz_to_note(float(high_p)),
                    }

                return {
                    "tempo": round(tempo_val),
                    "peak_str": f"{int(peak_sec//60)}:{int(peak_sec%60):02d}",
                    "energy_desc": energy_desc,
                    "duration_str": f"{int(dur//60)}:{int(dur%60):02d}",
                    "key": best_key,
                    "vocal_range": vocal_range,
                }

            logger.info("[audio] librosa 分析中...")
            info = await loop.run_in_executor(None, _librosa_analysis)
            logger.info("[audio] librosa 完成：key=%s range=%s", info.get("key"), info.get("vocal_range"))

            # ── 3. Demucs v4 (htdemucs) GPU 音軌分離 ────────────────────
            def _demucs_analysis():
                import sys
                import torch
                import soundfile as sf
                if "torchaudio" not in sys.modules:
                    from unittest.mock import MagicMock
                    sys.modules["torchaudio"] = MagicMock()
                    sys.modules["torchaudio.functional"] = MagicMock()
                from demucs.pretrained import get_model
                from demucs.apply import apply_model

                model = None
                try:
                    model = get_model("htdemucs")
                    model.eval()
                    data, sr_orig = sf.read(wav_path, always_2d=True)
                    waveform = torch.from_numpy(data.T).float()
                    if sr_orig != model.samplerate:
                        waveform = torch.from_numpy(np.array([
                            librosa.resample(waveform[c].numpy(), orig_sr=sr_orig, target_sr=model.samplerate)
                            for c in range(waveform.shape[0])
                        ])).float()
                    if waveform.shape[0] == 1:
                        waveform = waveform.repeat(2, 1)
                    with torch.no_grad():
                        sources = apply_model(model, waveform.unsqueeze(0), device="cuda", progress=False)
                    src = model.sources
                    def _stem(name):
                        return sources[0, src.index(name)].mean(0).cpu().numpy()
                    drums_np  = _stem("drums")
                    bass_np   = _stem("bass")
                    other_np  = _stem("other")
                    vocals_np = _stem("vocals")
                    sr_out = model.samplerate
                finally:
                    if model is not None:
                        del model
                    torch.cuda.empty_cache()
                    logger.info("[audio] Demucs VRAM 已釋放")

                def _to22k(arr):
                    return librosa.resample(arr, orig_sr=sr_out, target_sr=22050)

                drums_22k  = _to22k(drums_np)
                bass_22k   = _to22k(bass_np)
                other_22k  = _to22k(other_np)
                vocals_22k = _to22k(vocals_np)

                # ── 鼓組 ──────────────────────────────────────────────────
                stft_d = np.abs(librosa.stft(drums_22k))
                freqs = librosa.fft_frequencies(sr=22050)
                kick_e  = float(stft_d[freqs < 150].mean())
                hihat_e = float(stft_d[freqs > 5000].mean())
                if kick_e > hihat_e * 1.5:
                    drum_char = "低頻踢鼓厚實，節奏重量感強"
                elif hihat_e > kick_e * 1.5:
                    drum_char = "hi-hat 主導，節奏清脆密集"
                else:
                    drum_char = "踢鼓與 hi-hat 均衡分佈"

                # ── 貝斯 ──────────────────────────────────────────────────
                bass_f0, _, _ = librosa.pyin(bass_22k, fmin=30, fmax=400, sr=22050)
                valid_bass = bass_f0[~np.isnan(bass_f0)] if bass_f0 is not None else np.array([])
                bass_root = librosa.hz_to_note(float(np.median(valid_bass))) if len(valid_bass) > 10 else None

                # ── other（吉他/鋼琴/合成器）──────────────────────────────
                centroid = float(librosa.feature.spectral_centroid(y=other_22k, sr=22050).mean())
                chroma_std = float(librosa.feature.chroma_stft(y=other_22k, sr=22050).std())
                if centroid > 3000:
                    brightness = "高頻明亮（吉他/合成器清脆）"
                elif centroid > 1500:
                    brightness = "中頻飽滿（鋼琴/吉他層次豐富）"
                else:
                    brightness = "低中頻厚重（氛圍感強）"
                harmony = "和聲層次複雜" if chroma_std > 0.2 else "和聲線條精簡"
                other_char = f"{brightness}，{harmony}"

                # ── 人聲（獨立軌，比 HPSS 更準確）──────────────────────
                f0v, _, _ = librosa.pyin(vocals_22k, fmin=150, fmax=900, sr=22050)
                valid_f0v = f0v[~np.isnan(f0v)] if f0v is not None else np.array([])
                vocal_range = None
                if len(valid_f0v) > 20:
                    low_p, high_p = np.percentile(valid_f0v, [5, 95])
                    vocal_range = {
                        "low": librosa.hz_to_note(float(low_p)),
                        "high": librosa.hz_to_note(float(high_p)),
                    }
                rms_v = librosa.feature.rms(y=vocals_22k)[0]
                vocal_dynamic = "動態幅度大" if rms_v.std() / (rms_v.mean() + 1e-9) > 0.5 else "音量較為穩定"

                return {
                    "drum_char": drum_char,
                    "bass_root": bass_root,
                    "other_char": other_char,
                    "vocal_range": vocal_range,   # 覆蓋 librosa HPSS 估算
                    "vocal_dynamic": vocal_dynamic,
                }

            try:
                logger.info("[audio] Demucs htdemucs 分離中...")
                info.update(await loop.run_in_executor(None, _demucs_analysis))
                logger.info("[audio] Demucs 完成：drum=%s bass=%s", info.get("drum_char"), info.get("bass_root"))
            except Exception as e:
                logger.warning("[audio] Demucs 失敗（繼續）：%s", e)

            # ── 4. Whisper large-v3 GPU 轉錄 ────────────────────────────
            import whisper
            import torch

            model = None
            try:
                def _transcribe():
                    y_16k = librosa.load(wav_path, sr=16000, mono=True)[0]
                    m = whisper.load_model("large-v3", device="cuda")
                    r = m.transcribe(y_16k, language="ja", fp16=True, verbose=False)
                    return m, r

                logger.info("[audio] Whisper large-v3 轉錄中...")
                model, result = await loop.run_in_executor(None, _transcribe)
                info["transcript"] = result["text"].strip()[:600]
                info["segments"] = [
                    {"t": f"{int(s['start']//60)}:{int(s['start']%60):02d}", "text": s["text"].strip()}
                    for s in result.get("segments", [])[:10]
                ]
                logger.info("[audio] Whisper 完成：%d 字", len(info["transcript"]))
            finally:
                if model is not None:
                    del model
                torch.cuda.empty_cache()
                logger.info("[audio] Whisper VRAM 已釋放")

            return info

    except Exception as e:
        logger.error("[audio] 音訊分析失敗：%s", e)
        return {}
