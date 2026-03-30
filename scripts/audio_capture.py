# ============================================================
# KhutbaBox — Script de capture audio pour Windows
#
# Ce script tourne directement sur Windows (en dehors de Docker).
# Il capture le micro, détecte la voix, et envoie les morceaux
# audio au backend KhutbaBox qui tourne dans Docker.
#
# Installation des dépendances :
#   py -m pip install sounddevice requests
#
# Lancer le script :
#   py scripts/audio_capture.py
# ============================================================

import io
import wave
import struct

import numpy as np
import sounddevice as sd
import requests

# ── CONSTANTES ──
BACKEND_URL = "http://localhost/api/audio/chunk"
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_DURATION = 5       # secondes par morceau
SILENCE_THRESHOLD = 500  # seuil RMS pour détecter la voix

PREFIX = "[KhutbaBox Audio]"


def calculer_rms(audio_data: bytes) -> float:
    """Calcule le volume RMS d'un morceau audio."""
    count = len(audio_data) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack(f"<{count}h", audio_data)
    return (sum(s * s for s in samples) / count) ** 0.5


def audio_vers_wav(audio_data: bytes) -> bytes:
    """Emballe les données audio brutes dans un fichier WAV en mémoire."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # 16 bits = 2 octets
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio_data)
    return buffer.getvalue()


def envoyer_chunk(audio_data: bytes):
    """Envoie le morceau audio au backend via POST multipart."""
    wav_data = audio_vers_wav(audio_data)
    try:
        res = requests.post(
            BACKEND_URL,
            files={"file": ("chunk.wav", wav_data, "audio/wav")},
            timeout=10,
        )
        if res.ok:
            taille = res.json().get("taille_bytes", "?")
            print(f"{PREFIX} → Serveur : ok ({taille} bytes)")
        else:
            detail = res.json().get("detail", res.text)
            print(f"{PREFIX} → Erreur : {detail}")
    except requests.ConnectionError:
        print(f"{PREFIX} → Erreur : impossible de contacter le serveur")
    except Exception as e:
        print(f"{PREFIX} → Erreur : {e}")


def main():
    """Boucle principale : capture micro → détection voix → envoi."""
    print(f"{PREFIX} Micro démarré — en écoute...")

    try:
        while True:
            # Capturer 5 secondes d'audio (16 bits, mono, 16000 Hz)
            recording = sd.rec(
                frames=SAMPLE_RATE * CHUNK_DURATION,
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
            )
            sd.wait()  # attendre la fin de l'enregistrement

            # Convertir le tableau numpy en bytes bruts
            audio_data = recording.tobytes()
            rms = calculer_rms(audio_data)

            if rms > SILENCE_THRESHOLD:
                print(f"{PREFIX} Voix détectée, envoi...")
                envoyer_chunk(audio_data)
            else:
                print(f"{PREFIX} Silence...")

    except KeyboardInterrupt:
        print(f"\n{PREFIX} Arrêté par l'utilisateur.")


if __name__ == "__main__":
    main()
