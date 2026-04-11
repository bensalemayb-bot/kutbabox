"""
KhutbaBox — Moteur TTS hybride
ElevenLabs Flash v2.5 (fr, en, es, pt, tr) + Azure Neural TTS (ur, bs, sq)
"""

import os
import time
import asyncio
import logging

import httpx
import azure.cognitiveservices.speech as speechsdk

logger = logging.getLogger("khutbabox.tts")

# ── Configuration ──

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "westeurope")

ELEVENLABS_MODEL = "eleven_flash_v2_5"

# Voix ElevenLabs par défaut (Adam — multilingue)
ELEVENLABS_VOICE_MALE = os.getenv("ELEVENLABS_VOICE_MALE", "pNInz6obpgDQGcFmaJgB")
ELEVENLABS_VOICE_FEMALE = os.getenv("ELEVENLABS_VOICE_FEMALE", "EXAVITQu4vr4xnSDxMaL")

# Voix Azure Neural TTS
AZURE_TTS_VOICES_MALE = {
    "ur": "ur-PK-AsadNeural",
    "bs": "bs-BA-GoranNeural",
    "sq": "sq-AL-IlirNeural",
}
AZURE_TTS_VOICES_FEMALE = {
    "ur": "ur-PK-UzmaNeural",
    "bs": "bs-BA-VesnaNeural",
    "sq": "sq-AL-AnilaNeural",
}

# Langues → provider
TTS_PROVIDERS = {
    "fr": "elevenlabs",
    "en": "elevenlabs",
    "es": "elevenlabs",
    "pt": "elevenlabs",
    "tr": "elevenlabs",
    "ur": "azure",
    "bs": "azure",
    "sq": "azure",
}

# Sémaphore ElevenLabs : max 2 appels simultanés (évite erreur 429)
_elevenlabs_semaphore: asyncio.Semaphore | None = None


def init_tts():
    """Initialise le sémaphore. Appeler une fois au démarrage de FastAPI."""
    global _elevenlabs_semaphore
    _elevenlabs_semaphore = asyncio.Semaphore(2)


async def elevenlabs_tts(text: str, lang: str, voice: str = "male") -> bytes | None:
    """
    Génère de l'audio via ElevenLabs Flash v2.5 streaming.
    Retourne les bytes MP3 ou None si erreur.
    """
    voice_id = ELEVENLABS_VOICE_MALE if voice == "male" else ELEVENLABS_VOICE_FEMALE
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"

    debut = time.time()
    try:
        async with _elevenlabs_semaphore, httpx.AsyncClient() as client:
            response = await client.post(
                url,
                headers={
                    "xi-api-key": ELEVENLABS_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": ELEVENLABS_MODEL,
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                },
                params={"output_format": "mp3_44100_128"},
                timeout=30.0,
            )
            response.raise_for_status()
            audio = response.content
            latence = int((time.time() - debut) * 1000)
            logger.info(f"[TTS ELEVENLABS] {lang} — {len(audio)} bytes, {latence}ms")
            return audio
    except Exception as e:
        logger.error(f"[TTS ELEVENLABS] Erreur {lang} : {e}")
        return None


async def azure_tts(text: str, lang: str, voice: str = "male") -> bytes | None:
    """
    Génère de l'audio via Azure Neural TTS.
    Retourne les bytes WAV ou None si erreur.
    """
    if not AZURE_SPEECH_KEY:
        logger.warning(f"[TTS AZURE] Pas de clé Azure configurée")
        return None

    voices = AZURE_TTS_VOICES_MALE if voice == "male" else AZURE_TTS_VOICES_FEMALE
    voice_name = voices.get(lang)
    if not voice_name:
        logger.error(f"[TTS AZURE] Pas de voix configurée pour {lang}")
        return None

    debut = time.time()
    try:
        def _synthesize():
            speech_config = speechsdk.SpeechConfig(
                subscription=AZURE_SPEECH_KEY,
                region=AZURE_SPEECH_REGION,
            )
            speech_config.speech_synthesis_voice_name = voice_name
            synthesizer = speechsdk.SpeechSynthesizer(
                speech_config=speech_config,
                audio_config=None,
            )
            result = synthesizer.speak_text(text)
            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                return result.audio_data
            elif result.reason == speechsdk.ResultReason.Canceled:
                details = result.cancellation_details
                logger.error(f"[TTS AZURE] Annulé : {details.reason} — {details.error_details}")
            return None

        audio = await asyncio.to_thread(_synthesize)
        if audio:
            latence = int((time.time() - debut) * 1000)
            logger.info(f"[TTS AZURE] {lang} ({voice_name}) — {len(audio)} bytes, {latence}ms")
        return audio
    except Exception as e:
        logger.error(f"[TTS AZURE] Erreur {lang} : {e}")
        return None


async def generer_tts(text: str, lang: str, voice: str = "male") -> tuple[bytes | None, str]:
    """
    Route vers le bon provider TTS selon la langue.
    Retourne (audio_bytes, format) ou (None, "").
    format = "mp3" pour ElevenLabs, "wav" pour Azure.
    """
    provider = TTS_PROVIDERS.get(lang, "elevenlabs")

    if provider == "azure":
        audio = await azure_tts(text, lang, voice)
        return (audio, "wav") if audio else (None, "")
    else:
        audio = await elevenlabs_tts(text, lang, voice)
        return (audio, "mp3") if audio else (None, "")
