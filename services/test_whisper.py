from faster_whisper import WhisperModel  # type: ignore[import-not-found]

print("Loading model... (first run downloads ~150MB)")
model = WhisperModel("base", device="cpu", compute_type="int8")

audio_path = "test_audio.m4a"  # <-- change this to match your actual filename

print("Transcribing...")
segments, info = model.transcribe(audio_path)

print(f"\nDetected language: {info.language} (confidence: {info.language_probability:.2f})")
print("\nTranscript:")
for segment in segments:
    print(f"[{segment.start:.2f}s -> {segment.end:.2f}s] {segment.text}")