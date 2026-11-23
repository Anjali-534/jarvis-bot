"""Compare speech-to-text engines on recorded WAV files.

    python -m jarvis.benchmark                 # data/samples/*.wav through google and whisper
    python -m jarvis.benchmark --engines whisper --runs 3
    python -m jarvis.benchmark --synth         # no recordings yet: make 5 samples with the Windows voice

Record your own voice first by saying "record samples" to Jarvis (saved in data/samples/, with the
expected text in sampleN.txt next to each WAV).
"""
import argparse
import re
import statistics
import sys
import time
import wave
from pathlib import Path

import config

RATE = 16000


def load_wav(path):
    """16 kHz mono 16-bit PCM bytes and the duration in seconds, converting if needed."""
    import numpy as np

    from jarvis.audio import resample

    with wave.open(str(path)) as wav:
        channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
        raw = wav.readframes(wav.getnframes())
    if width != 2:
        raise ValueError(f"{path.name}: only 16-bit WAV files are supported")
    if channels > 1:
        samples = np.frombuffer(raw, dtype=np.int16).reshape(-1, channels).mean(axis=1).astype(np.int16)
        raw = samples.tobytes()
    if rate != RATE:
        raw = resample(raw, rate, RATE)
    return raw, len(raw) / 2 / RATE


def expected_text(path):
    txt = path.with_suffix(".txt")
    return txt.read_text(encoding="utf-8").strip() if txt.exists() else ""


def word_accuracy(expected, heard):
    """Share of expected words that were heard (rough, order-insensitive)."""
    norm = lambda s: re.findall(r"[a-z0-9]+", (s or "").lower().replace("five", "5"))  # noqa: E731
    want, got = norm(expected), set(norm(heard))
    return sum(w in got for w in want) / len(want) if want else None


def synth_samples(folder):
    """Make 5 samples with the Windows voice (no microphone needed)."""
    import pyttsx3

    from jarvis.skills.system import SAMPLE_PHRASES

    folder.mkdir(parents=True, exist_ok=True)
    engine = pyttsx3.init()
    for number, phrase in enumerate(SAMPLE_PHRASES, 1):
        engine.save_to_file(phrase, str(folder / f"sample{number}.wav"))
        (folder / f"sample{number}.txt").write_text(phrase, encoding="utf-8")
    engine.runAndWait()


def make_engine(name, language):
    """(engine, load_seconds). Whisper is loaded synchronously here so load time is measured separately."""
    from jarvis import stt

    started = time.perf_counter()
    if name == "google":
        return stt.GoogleSTT(), 0.0
    if name == "whisper":
        engine = stt.WhisperSTT(fallback=None, preload_language=None)
        model = stt.whisper_model_name(language)
        engine.models[model] = engine._load(model)
        return engine, time.perf_counter() - started
    raise ValueError(f"unknown engine {name!r}")


def run(files, engines, language="en-IN", runs=1, out=print):
    """Benchmark each engine on each file. Returns {engine: [latency, ...]}."""
    results = {}
    samples = [(path, *load_wav(path)) for path in files]
    for name in engines:
        try:
            engine, load_seconds = make_engine(name, language)
        except Exception as exc:
            out(f"\n{name}: couldn't start ({exc})")
            continue
        detail = ""
        if getattr(engine, "models", None):
            detail = f" ({engine.device}, {engine.compute_type}, model {list(engine.models)[0]}, " \
                     f"loaded in {load_seconds:.1f}s)"
        out(f"\n{name}{detail}")
        latencies, accuracies = [], []
        for path, pcm, seconds in samples:
            times, text = [], ""
            for _ in range(runs):
                started = time.perf_counter()
                text = engine.transcribe(pcm, language)
                times.append(time.perf_counter() - started)
            best = min(times)
            latencies.append(best)
            accuracy = word_accuracy(expected_text(path), text)
            if accuracy is not None:
                accuracies.append(accuracy)
            score = f" {accuracy:.0%} words" if accuracy is not None else ""
            out(f"  {path.name:14} audio {seconds:4.1f}s  ->  {best:5.2f}s{score}  '{text}'")
        if latencies:
            avg = statistics.mean(latencies)
            acc = f", {statistics.mean(accuracies):.0%} of words right" if accuracies else ""
            out(f"  average {avg:.2f}s, fastest {min(latencies):.2f}s, slowest {max(latencies):.2f}s{acc}")
            results[name] = latencies
    if len(results) > 1:
        fastest = min(results, key=lambda n: statistics.mean(results[n]))
        out(f"\nFastest on this machine: {fastest}. Set STT_ENGINE={fastest} in .env.")
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compare speech-to-text engines on WAV samples.")
    parser.add_argument("--dir", type=Path, default=config.SAMPLES_DIR, help="folder of .wav files")
    parser.add_argument("--engines", default="google,whisper", help="comma-separated: google,whisper")
    parser.add_argument("--language", default="en-IN")
    parser.add_argument("--runs", type=int, default=1, help="runs per file (the best is reported)")
    parser.add_argument("--synth", action="store_true", help="create 5 samples with the Windows voice first")
    args = parser.parse_args(argv)

    if args.synth:
        synth_samples(args.dir)
    files = sorted(args.dir.glob("*.wav"))[:5]
    if not files:
        print(f"No WAV files in {args.dir}. Say \"record samples\" to Jarvis, or run with --synth.")
        return 1
    print(f"Benchmarking {len(files)} file(s) from {args.dir}")
    run(files, [e.strip() for e in args.engines.split(",") if e.strip()], args.language, args.runs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
