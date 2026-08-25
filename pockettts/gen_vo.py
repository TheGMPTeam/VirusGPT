import sys, json, base64, subprocess, tempfile, os
import torch
import soundfile as sf
import imageio_ffmpeg

from pocket_tts import TTSModel

LINES = [
    "The ocean is glowing — and it's alive.",          # 0 hook
    "Waves that glow blue in the dark.",                # 1 scene0
    "Every drop is alive with light.",                  # 2 scene1
    "This is the living sea.",                          # 3 scene2
]
VOICE_WAV = "/Users/Master/virusgpt-mac/pockettts/voices/david_attenborough.wav"
OUT_WAV = ["/tmp/vo0.wav", "/tmp/vo1.wav", "/tmp/vo2.wav", "/tmp/vo3.wav"]
OUT_MP3 = ["/tmp/vo0.mp3", "/tmp/vo1.mp3", "/tmp/vo2.mp3", "/tmp/vo3.mp3"]

ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
print("ffmpeg:", ffmpeg, flush=True)

print("Loading model (english, cpu)...", flush=True)
model = TTSModel.load_model(language="english", quantize=False)
print("Loaded. sample_rate=", model.sample_rate, flush=True)

print("Building voice state from", VOICE_WAV, flush=True)
voice_state = model.get_state_for_audio_prompt(VOICE_WAV, truncate=True)
print("Voice state ready.", flush=True)

b64 = []
for i, text in enumerate(LINES):
    print(f"[{i}] generating: {text!r}", flush=True)
    audio = model.generate_audio(voice_state, text, frames_after_eos=2, copy_state=True)
    # audio shape [channels, samples]
    if audio.dim() == 1:
        audio = audio.unsqueeze(0)
    wav = OUT_WAV[i]
    sf.write(wav, audio.squeeze(0).cpu().numpy(), model.sample_rate)
    mp3 = OUT_MP3[i]
    subprocess.run([ffmpeg, "-y", "-i", wav, "-codec:a", "libmp3lame", "-b:a", "192k", mp3],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    dur = audio.shape[-1] / model.sample_rate
    size = os.path.getsize(mp3)
    print(f"[{i}] wrote {mp3} ({size} bytes, {dur:.2f}s)", flush=True)
    with open(mp3, "rb") as f:
        b64.append(base64.b64encode(f.read()).decode("ascii"))

result = {"vo": b64}
with open("/tmp/vo_b64.json", "w") as f:
    json.dump(result, f)
print("DONE", flush=True)
