# ============================================================
# KhutbaBox — Script de capture audio pour Windows
#
# Ce script tourne directement sur Windows (en dehors de Docker).
# Il capture le micro et envoie l'audio au backend KhutbaBox.
#
# Deux modes disponibles :
#   --mode websocket (défaut) : streaming continu via WebSocket
#   --mode legacy             : chunks de 5s via POST HTTP
#
# Installation des dépendances :
#   py -m pip install sounddevice numpy websockets requests
#
# Lancer le script :
#   py scripts/audio_capture.py
#   py scripts/audio_capture.py --mode legacy
#   py scripts/audio_capture.py --url ws://192.168.1.50/ws/audio-stream
# ============================================================

import io
import wave
import struct
import asyncio
import logging
import argparse

import numpy as np
import sounddevice as sd
import requests
import websockets

# ── LOGGER ──
logging.basicConfig(level=logging.INFO, format="[KhutbaBox Audio] %(message)s")
logger = logging.getLogger("audio_capture")

# ── CONSTANTES ──
SAMPLE_RATE = 16000
CHANNELS = 1
SILENCE_THRESHOLD = 500   # seuil RMS int16 pour détecter la voix

# Mode WebSocket
BACKEND_WS_URL = "ws://localhost/ws/audio-stream"
FRAME_DURATION_MS = 100   # ~100ms par frame
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000)  # 1600 samples
RECONNECT_DELAY = 3       # secondes entre les tentatives de reconnexion

# Mode Legacy (POST)
BACKEND_POST_URL = "http://localhost/api/audio/chunk"
CHUNK_DURATION = 5        # secondes par morceau


def lister_micros():
    """Affiche tous les micros disponibles sur Windows avec leur numéro."""
    logger.info("Micros disponibles :")
    print("-" * 50)
    devices = sd.query_devices()
    micro_trouve = False
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0:
            defaut = " ← DÉFAUT" if i == sd.default.device[0] else ""
            print(f"  [{i}] {d['name']}{defaut}")
            micro_trouve = True
    if not micro_trouve:
        print("  AUCUN micro détecté !")
    print("-" * 50)
    logger.info(f"Micro utilisé : [{sd.default.device[0]}]")
    print()


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
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio_data)
    return buffer.getvalue()


# ============================================================
# MODE WEBSOCKET — Streaming continu (~100ms par frame)
# ============================================================

async def stream_audio(url: str):
    """
    Capture le micro en continu et envoie les frames audio
    au backend via WebSocket. Reconnexion automatique.
    """
    micro = sd.default.device[0]
    if micro is None or micro < 0:
        logger.error("Aucun micro par défaut détecté ! Branche un micro USB et réessaie.")
        return

    lister_micros()
    logger.info(f"Mode WebSocket — streaming vers {url}")
    logger.info("Ctrl+C pour arrêter")

    while True:
        try:
            async with websockets.connect(url) as ws:
                logger.info("Connecté au backend")

                # File d'attente pour passer les frames du callback audio → boucle async
                queue = asyncio.Queue()

                def audio_callback(indata, frames, time_info, status):
                    """Callback sounddevice : capture une frame audio."""
                    if status:
                        logger.warning(f"Audio status : {status}")
                    # Convertir float32 → int16
                    int16_data = (indata[:, 0] * 32767).astype(np.int16)
                    queue.put_nowait(int16_data.tobytes())

                # Ouvrir le flux audio micro
                stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="float32",
                    blocksize=FRAME_SIZE,
                    device=micro,
                    callback=audio_callback,
                )

                with stream:
                    logger.info(f"Micro ouvert — frames de {FRAME_DURATION_MS}ms ({FRAME_SIZE} samples)")
                    while True:
                        # Récupérer la frame depuis le callback
                        audio_bytes = await queue.get()
                        rms = calculer_rms(audio_bytes)

                        if rms > SILENCE_THRESHOLD:
                            # Voix détectée → envoyer au backend
                            await ws.send(audio_bytes)
                            logger.debug(f"Envoyé {len(audio_bytes)} bytes (RMS={rms:.0f})")
                        # Silence → on n'envoie rien

        except websockets.ConnectionClosed:
            logger.warning("WebSocket déconnecté")
        except ConnectionRefusedError:
            logger.warning("Backend non disponible")
        except Exception as e:
            logger.error(f"Erreur : {e}")

        logger.info(f"Reconnexion dans {RECONNECT_DELAY}s...")
        await asyncio.sleep(RECONNECT_DELAY)


# ============================================================
# MODE LEGACY — Chunks de 5 secondes via POST HTTP
# ============================================================

def envoyer_chunk(audio_data: bytes):
    """Envoie le morceau audio au backend via POST multipart."""
    wav_data = audio_vers_wav(audio_data)
    try:
        res = requests.post(
            BACKEND_POST_URL,
            files={"file": ("chunk.wav", wav_data, "audio/wav")},
            timeout=10,
        )
        if res.ok:
            taille = res.json().get("taille_bytes", "?")
            logger.info(f"Serveur : ok ({taille} bytes)")
        else:
            detail = res.json().get("detail", res.text)
            logger.warning(f"Erreur serveur : {detail}")
    except requests.ConnectionError:
        logger.warning("Impossible de contacter le serveur")
    except Exception as e:
        logger.error(f"Erreur : {e}")


def main_legacy():
    """Boucle principale mode Legacy : capture micro → détection voix → envoi POST."""
    lister_micros()

    micro = sd.default.device[0]
    if micro is None or micro < 0:
        logger.error("Aucun micro par défaut détecté ! Branche un micro USB et réessaie.")
        return

    logger.info(f"Mode Legacy — chunks de {CHUNK_DURATION}s vers {BACKEND_POST_URL}")
    logger.info("Ctrl+C pour arrêter")

    try:
        while True:
            recording = sd.rec(
                frames=SAMPLE_RATE * CHUNK_DURATION,
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                device=micro,
            )
            sd.wait()

            int16_data = (recording.flatten() * 32767).astype(np.int16)
            audio_data = int16_data.tobytes()
            rms = calculer_rms(audio_data)

            if rms < 5:
                logger.warning("Micro muet — vérifier les paramètres Windows")
            elif rms > SILENCE_THRESHOLD:
                logger.info(f"Voix détectée (RMS={rms:.0f}), envoi...")
                envoyer_chunk(audio_data)
            else:
                logger.debug(f"Silence (RMS={rms:.0f})")

    except KeyboardInterrupt:
        logger.info("Arrêté par l'utilisateur.")


# ============================================================
# POINT D'ENTRÉE
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KhutbaBox — Capture audio micro")
    parser.add_argument(
        "--mode",
        choices=["websocket", "legacy"],
        default="websocket",
        help="Mode d'envoi : websocket (streaming) ou legacy (POST chunks 5s)",
    )
    parser.add_argument(
        "--url",
        default=BACKEND_WS_URL,
        help=f"URL du backend WebSocket (défaut: {BACKEND_WS_URL})",
    )
    args = parser.parse_args()

    if args.mode == "websocket":
        asyncio.run(stream_audio(args.url))
    else:
        main_legacy()
