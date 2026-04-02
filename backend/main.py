"""
KhutbaBox — Backend principal
Système de traduction en temps réel des sermons de mosquée.
Développé par Boualem — IA Factory — Genève.
"""

import os
import io
import time
import json
import wave
import base64
import struct
import asyncio
import logging
import threading
from datetime import datetime, timezone
from collections import deque

try:
    import pyaudio
    HAS_PYAUDIO = True
except ImportError:
    HAS_PYAUDIO = False

try:
    import azure.cognitiveservices.speech as speechsdk
    AZURE_AVAILABLE = True
except ImportError:
    AZURE_AVAILABLE = False

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File
from fastapi.staticfiles import StaticFiles
import openai
import google.generativeai as genai
import uvicorn

# Logger principal
logger = logging.getLogger("khutbabox")
logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")


# ============================================================
# 1. CONFIGURATION — Clés API depuis le fichier .env
# ============================================================

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ADMIN_PIN = os.getenv("ADMIN_PIN", "0000")
MOSQUE_NAME = os.getenv("MOSQUE_NAME", "Mosquée")
BOX_ID = os.getenv("BOX_ID", "khutbabox-001")

# Azure Speech Translation (optionnel — fallback sur Whisper+Gemini si absent)
AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "westeurope")
AZURE_ENABLED = bool(AZURE_SPEECH_KEY) and AZURE_AVAILABLE

if AZURE_ENABLED:
    logger.info("✅ Mode Azure Speech Translation activé")
elif not AZURE_AVAILABLE:
    logger.warning("⚠️ azure-cognitiveservices-speech non installé — mode Legacy forcé")
else:
    logger.info("⚠️ Mode Legacy (Whisper + Gemini) — AZURE_SPEECH_KEY non configurée")

openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-2.5-flash")

app = FastAPI(title="KhutbaBox", version="1.0.0")


# ============================================================
# 2. GLOSSAIRE ISLAMIQUE — 36 termes protégés
#    Ces termes ne sont JAMAIS traduits librement.
# ============================================================

GLOSSAIRE = {
    "الله": "Allah",
    "القرآن": "le Coran",
    "الصلاة": "la Salat",
    "إن شاء الله": "Inch'Allah",
    "بسم الله": "Bismillah",
    "الحمد لله": "Al-Hamdoulillah",
    "سبحان الله": "Soubhan'Allah",
    "الله أكبر": "Allahou Akbar",
    "لا إله إلا الله": "La ilaha illa Allah",
    "محمد": "Muhammad",
    "صلى الله عليه وسلم": "Salla Allahou Alayhi wa Sallam",
    "رسول الله": "Rassoul Allah",
    "السلام عليكم": "Assalamou Alaykoum",
    "الجنة": "le Paradis (Jannah)",
    "النار": "l'Enfer (Jahannam)",
    "الحج": "le Hajj",
    "الزكاة": "la Zakat",
    "الصيام": "le Jeûne (Siyam)",
    "رمضان": "Ramadan",
    "الوضوء": "les Ablutions (Woudou)",
    "المسجد": "la Mosquée",
    "الإمام": "l'Imam",
    "الخطبة": "la Khoutba",
    "الجمعة": "le Vendredi (Joumou'a)",
    "السنة": "la Sunna",
    "الحديث": "le Hadith",
    "الشهادة": "la Chahada",
    "التوبة": "le Repentir (Tawba)",
    "الدعاء": "l'Invocation (Dou'a)",
    "الأذان": "l'Adhan",
    "التقوى": "la Piété (Taqwa)",
    "الشريعة": "la Charia",
    "الفقه": "le Fiqh",
    "العمرة": "la Omra",
    "الصدقة": "la Sadaqa",
    "ما شاء الله": "Masha'Allah",
}

# Langues supportées — 8 langues cibles (code → nom complet pour les prompts)
LANGUES = {
    "fr": "français",
    "en": "anglais",
    "es": "espagnol",
    "pt": "portugais",
    "tr": "turc",
    "ur": "ourdou",
    "bs": "bosnien",
    "sq": "albanais",
}

# TTS provider par langue (ElevenLabs Flash pour 5, Azure Neural pour 3)
TTS_PROVIDERS = {
    "fr": "elevenlabs",
    "en": "elevenlabs",
    "es": "elevenlabs",
    "pt": "elevenlabs",
    "tr": "elevenlabs",
    "ur": "azure",    # Azure Neural TTS (ur-PK)
    "bs": "azure",    # Azure Neural TTS (bs-BA)
    "sq": "azure",    # Azure Neural TTS (sq-AL)
}

# Voix Azure Neural TTS pour les 3 langues
AZURE_TTS_VOICES = {
    "ur": "ur-PK-AsadNeural",
    "bs": "bs-BA-GoranNeural",
    "sq": "sq-AL-IlirNeural",
}

# Voix ElevenLabs par langue (Flash v2.5 multilingue — même voix, détecte la langue auto)
ELEVENLABS_VOICES = {
    "fr": os.getenv("ELEVENLABS_VOICE_MALE", "pNInz6obpgDQGcFmaJgB"),  # Adam
    "en": os.getenv("ELEVENLABS_VOICE_MALE", "pNInz6obpgDQGcFmaJgB"),
    "es": os.getenv("ELEVENLABS_VOICE_MALE", "pNInz6obpgDQGcFmaJgB"),
    "pt": os.getenv("ELEVENLABS_VOICE_MALE", "pNInz6obpgDQGcFmaJgB"),
    "tr": os.getenv("ELEVENLABS_VOICE_MALE", "pNInz6obpgDQGcFmaJgB"),
}
ELEVENLABS_MODEL = "eleven_flash_v2_5"


# ============================================================
# 3. ÉTAT GLOBAL
# ============================================================

# --- Session ---
session = {
    "active": False,
    "started_at": None,
    "active_langs": list(LANGUES.keys()),
    "mosque_name": MOSQUE_NAME,
    "mode": "auto",
}

# --- Monitoring ---
latences = deque(maxlen=20)
historique = deque(maxlen=50)

# --- Clients WebSocket connectés ---
# Clé = id unique, Valeur = {"ws": WebSocket, "lang": str, "voice": str}
clients: dict[str, dict] = {}

# --- File d'attente audio (créée au démarrage dans on_startup) ---
audio_queue: asyncio.Queue = None

# --- Suivi du silence ---
last_voice_time: float = 0.0
SILENCE_TIMEOUT = 120  # 2 minutes en secondes

# --- Paramètres audio micro ---
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_DURATION = 5       # secondes par morceau
AUDIO_FORMAT = pyaudio.paInt16 if HAS_PYAUDIO else 8
FRAMES_PER_READ = 1024
SILENCE_THRESHOLD = 500  # seuil RMS pour détecter la voix


# ============================================================
# FONCTIONS UTILITAIRES
# ============================================================

def calculer_rms(audio_data: bytes) -> float:
    """Calcule le volume RMS d'un morceau audio pour détecter voix/silence."""
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


# ============================================================
# 3a. BROADCAST — Envoi des traductions aux smartphones
# ============================================================

async def broadcast_translation(msg_type: str, lang: str, text: str = "", audio_b64: str = "", audio_format: str = ""):
    """
    Envoie un message WebSocket à tous les clients connectés sur cette langue.
    msg_type : "partial", "final_text" ou "audio_chunk"
    """
    payload = {"type": msg_type, "lang": lang}
    if msg_type == "audio_chunk":
        payload["data"] = audio_b64
        payload["format"] = audio_format
    else:
        payload["text"] = text

    message = json.dumps(payload)
    for info in list(clients.values()):
        if info["lang"] == lang:
            try:
                await info["ws"].send_text(message)
            except Exception:
                pass


# ============================================================
# 3b. AZURE SPEECH TRANSLATOR — STT arabe + traduction streaming
# ============================================================

class AzureSpeechTranslator:
    """
    Gère le pipeline Azure Speech Translation streaming.
    STT arabe + traduction simultanée vers 8 langues en un seul appel.
    """

    TARGET_LANGUAGES = {
        "fr": "fr",
        "en": "en",
        "es": "es",
        "pt": "pt",
        "tr": "tr",
        "ur": "ur",
        "bs": "bs",
        "sq": "sq",
    }

    def __init__(self, speech_key: str, speech_region: str, event_loop: asyncio.AbstractEventLoop):
        self.loop = event_loop

        # Config traduction : source arabe → 8 langues cibles
        translation_config = speechsdk.translation.SpeechTranslationConfig(
            subscription=speech_key,
            region=speech_region,
        )
        translation_config.speech_recognition_language = "ar-SA"
        for lang_code in self.TARGET_LANGUAGES.values():
            translation_config.add_target_language(lang_code)

        # Stream audio custom : PCM 16kHz 16-bit mono
        audio_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=16000,
            bits_per_sample=16,
            channels=1,
        )
        self.push_stream = speechsdk.audio.PushAudioInputStream(stream_format=audio_format)
        audio_config = speechsdk.audio.AudioConfig(stream=self.push_stream)

        # Recognizer
        self.recognizer = speechsdk.translation.TranslationRecognizer(
            translation_config=translation_config,
            audio_config=audio_config,
        )

        # Callbacks
        self.recognizer.recognizing.connect(self._on_recognizing)
        self.recognizer.recognized.connect(self._on_recognized)
        self.recognizer.canceled.connect(self._on_canceled)

        logger.info("Azure Speech Translator initialisé (source: ar-SA, cibles: 8 langues)")

    def start(self):
        """Démarre la reconnaissance continue."""
        self.recognizer.start_continuous_recognition()
        logger.info("Azure Speech Translation — reconnaissance continue démarrée")

    def push_audio(self, audio_data: bytes):
        """Pousse des chunks audio PCM 16kHz 16-bit mono dans le stream."""
        self.push_stream.write(audio_data)

    def stop(self):
        """Arrête la reconnaissance continue et ferme le stream."""
        self.recognizer.stop_continuous_recognition()
        self.push_stream.close()
        logger.info("Azure Speech Translation — arrêté")

    def _on_recognizing(self, evt):
        """Texte partiel — envoie aux smartphones via WebSocket."""
        if evt.result.reason == speechsdk.ResultReason.TranslatingSpeech:
            translations = evt.result.translations
            logger.debug(f"[PARTIEL] {len(translations)} langues")
            for lang, text in translations.items():
                if text.strip():
                    asyncio.run_coroutine_threadsafe(
                        broadcast_translation("partial", lang, text=text),
                        self.loop,
                    )

    def _on_recognized(self, evt):
        """Phrase complète — envoie le texte final + déclenche le TTS."""
        if evt.result.reason == speechsdk.ResultReason.TranslatedSpeech:
            translations = evt.result.translations
            texte_arabe = evt.result.text
            logger.info(f'[AZURE FINAL] Arabe : "{texte_arabe[:80]}"')

            # Poster le traitement TTS dans la boucle asyncio principale
            asyncio.run_coroutine_threadsafe(
                self._process_recognized(texte_arabe, dict(translations)),
                self.loop,
            )

    async def _process_recognized(self, texte_arabe: str, translations: dict):
        """Traite une phrase reconnue : envoie texte final + TTS pour chaque langue."""
        for lang, text in translations.items():
            if not text.strip():
                continue
            logger.info(f'[AZURE FINAL] {lang} : "{text[:80]}"')

            # Envoyer le texte final immédiatement
            await broadcast_translation("final_text", lang, text=text)

            # Générer et envoyer l'audio TTS
            audio, fmt = await generer_tts(text, lang)
            if audio:
                audio_b64 = base64.b64encode(audio).decode()
                await broadcast_translation("audio_chunk", lang, audio_b64=audio_b64, audio_format=fmt)
            else:
                logger.warning(f"[TTS] Pas d'audio pour {lang} — envoi texte uniquement")

        # Enregistrer dans le monitoring
        historique.append({
            "heure": datetime.now(timezone.utc).isoformat(),
            "texte_arabe": texte_arabe,
            "traductions": translations,
            "mode": "azure",
        })

    def _on_canceled(self, evt):
        """Erreur Azure — log pour debugging."""
        cancellation = evt.result
        logger.error(f"[AZURE ANNULÉ] Raison : {cancellation.reason}")
        if cancellation.reason == speechsdk.CancellationReason.Error:
            logger.error(f"[AZURE ERREUR] Code : {cancellation.error_code}")
            logger.error(f"[AZURE ERREUR] Détails : {cancellation.error_details}")


# ============================================================
# 3c. CAPTURE AUDIO — Thread séparé (micro USB)
# ============================================================

def thread_capture_audio(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue):
    """
    Tourne en permanence dans un thread séparé.
    Capture le micro par morceaux de 5 secondes.
    - Si voix détectée → envoie le morceau au pipeline.
    - Mode auto : démarre la session à la première voix,
      l'arrête après 2 minutes de silence.
    """
    global last_voice_time

    if not HAS_PYAUDIO:
        logger.info("PyAudio non installé (normal dans Docker)")
        logger.info("L'audio sera reçu via POST /api/audio/chunk ou WS /ws/audio-stream")
        return

    pa = pyaudio.PyAudio()
    try:
        stream = pa.open(
            format=AUDIO_FORMAT,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=FRAMES_PER_READ,
        )
    except OSError:
        logger.warning("Pas de micro détecté (normal dans Docker)")
        logger.info("L'audio sera reçu via POST /api/audio/chunk ou WS /ws/audio-stream")
        pa.terminate()
        return

    nb_lectures_par_chunk = (SAMPLE_RATE * CHUNK_DURATION) // FRAMES_PER_READ

    try:
        while True:
            # En mode manuel, si la session est inactive, on attend
            if session["mode"] == "manual" and not session["active"]:
                time.sleep(0.5)
                continue

            # Capturer 5 secondes d'audio
            frames = []
            for _ in range(nb_lectures_par_chunk):
                data = stream.read(FRAMES_PER_READ, exception_on_overflow=False)
                frames.append(data)

            audio_data = b"".join(frames)
            rms = calculer_rms(audio_data)

            if rms > SILENCE_THRESHOLD:
                # === VOIX DÉTECTÉE ===
                last_voice_time = time.time()

                # Mode auto : démarrer la session automatiquement
                if session["mode"] == "auto" and not session["active"]:
                    session["active"] = True
                    session["started_at"] = datetime.now(timezone.utc).isoformat()

                # Envoyer le morceau au pipeline de traduction
                if session["active"]:
                    asyncio.run_coroutine_threadsafe(
                        queue.put(audio_data), loop
                    )
            else:
                # === SILENCE ===
                # Mode auto : arrêter après 2 minutes de silence
                if (
                    session["mode"] == "auto"
                    and session["active"]
                    and last_voice_time > 0
                    and time.time() - last_voice_time > SILENCE_TIMEOUT
                ):
                    session["active"] = False
                    session["started_at"] = None

    except Exception as e:
        logger.error(f"[ERREUR MICRO] {e}")
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()


# ============================================================
# 5. TRANSCRIPTION LEGACY — OpenAI Whisper (arabe)
# ============================================================

async def transcrire(audio_data: bytes) -> str:
    """Envoie l'audio à Whisper et retourne le texte arabe."""
    wav_data = audio_vers_wav(audio_data)
    audio_file = io.BytesIO(wav_data)
    audio_file.name = "chunk.wav"

    response = await asyncio.to_thread(
        openai_client.audio.transcriptions.create,
        model="whisper-1",
        file=audio_file,
        language="ar",
    )
    return response.text



# ============================================================
# 6. TRADUCTION LEGACY — Gemini Flash + glossaire protégé
# ============================================================

async def traduire(texte_arabe: str, langue_cible: str) -> str:
    """
    Traduit le texte arabe dans la langue demandée.
    Le glossaire islamique est injecté dans le prompt
    pour que les 36 termes soient toujours conservés.
    """
    glossaire_str = "\n".join(
        f"  - {ar} → {trad}" for ar, trad in GLOSSAIRE.items()
    )
    nom_langue = LANGUES.get(langue_cible, langue_cible)

    prompt = (
        f"Tu es un traducteur professionnel spécialisé dans les sermons "
        f"islamiques (khoutba).\n\n"
        f"RÈGLES STRICTES :\n"
        f"1. Traduis le texte arabe ci-dessous en {nom_langue}.\n"
        f"2. Utilise un registre solennel et respectueux.\n"
        f"3. Aucune traduction créative ni interprétation libre.\n"
        f"4. Les termes islamiques suivants doivent être conservés "
        f"EXACTEMENT tels quels dans la traduction :\n"
        f"{glossaire_str}\n\n"
        f"TEXTE ARABE À TRADUIRE :\n«{texte_arabe}»\n\n"
        f"Réponds uniquement avec la traduction, sans commentaire "
        f"ni explication."
    )
    response = await asyncio.to_thread(
        gemini_model.generate_content, prompt
    )
    return response.text


# ============================================================
# 7. SYNTHÈSE VOCALE — TTS hybride (ElevenLabs Flash + Azure Neural)
# ============================================================

# --- 7a. ElevenLabs Legacy (ancien modèle, gardé comme fallback) ---

async def elevenlabs_tts_legacy(texte: str, voice_id: str = "pNInz6obpgDQGcFmaJgB") -> bytes:
    """Ancien TTS ElevenLabs (eleven_multilingual_v2). Fallback uniquement."""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "text": texte,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.6, "similarity_boost": 0.8},
            },
            params={"output_format": "mp3_44100_128"},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.content


# --- 7b. ElevenLabs Flash v2.5 (streaming, ~75ms latence) ---

async def elevenlabs_tts_stream(text: str, lang: str) -> bytes | None:
    """
    Génère de l'audio via ElevenLabs Flash v2.5 streaming.
    Retourne les bytes audio MP3 complets ou None si erreur.
    """
    voice_id = ELEVENLABS_VOICES.get(lang, ELEVENLABS_VOICES["fr"])
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"

    debut = time.time()
    try:
        async with httpx.AsyncClient() as client:
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


# --- 7c. Azure Neural TTS (~200ms latence) ---

async def azure_tts(text: str, lang: str) -> bytes | None:
    """
    Génère de l'audio via Azure Neural TTS.
    Retourne les bytes audio WAV ou None si erreur/non configuré.
    """
    if not AZURE_ENABLED:
        logger.warning(f"[TTS AZURE] Demandé pour {lang} mais Azure non configuré")
        return None

    voice_name = AZURE_TTS_VOICES.get(lang)
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
            # Sortie en mémoire (pas de fichier)
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
            return None

        audio = await asyncio.to_thread(_synthesize)
        if audio:
            latence = int((time.time() - debut) * 1000)
            logger.info(f"[TTS AZURE] {lang} ({voice_name}) — {len(audio)} bytes, {latence}ms")
        return audio
    except Exception as e:
        logger.error(f"[TTS AZURE] Erreur {lang} : {e}")
        return None


# --- 7d. Routeur TTS principal ---

async def generer_tts(text: str, lang: str) -> tuple[bytes | None, str]:
    """
    Route vers le bon provider TTS selon la langue.
    Retourne (audio_bytes, format) ou (None, "") si erreur/non disponible.
    format = "mp3" pour ElevenLabs, "wav" pour Azure
    """
    provider = TTS_PROVIDERS.get(lang, "elevenlabs")

    if provider == "azure":
        audio = await azure_tts(text, lang)
        return (audio, "wav") if audio else (None, "")
    else:
        audio = await elevenlabs_tts_stream(text, lang)
        return (audio, "mp3") if audio else (None, "")


# ============================================================
# 8. PIPELINE LEGACY — Transcription → Traduction → Voix → Envoi
#    Utilisé quand Azure Speech Translation n'est pas configuré.
# ============================================================

async def pipeline_traduction(audio_data: bytes):
    """Traite un morceau audio de 5 secondes à travers le pipeline Legacy."""
    debut = time.time()

    # --- Étape 1 : Transcription arabe ---
    texte_arabe = await transcrire(audio_data)
    logger.info(f'[WHISPER] Transcription : "{texte_arabe}"')
    if not texte_arabe.strip():
        logger.debug("[WHISPER] Texte vide — chunk ignoré")
        return

    # --- Étape 2 : Regrouper les clients par (langue, voix) ---
    groupes: dict[tuple[str, str], list[WebSocket]] = {}
    for info in list(clients.values()):
        cle = (info["lang"], info["voice"])
        groupes.setdefault(cle, []).append(info["ws"])

    # --- Étape 3 : Traduire + TTS hybride + envoyer pour chaque groupe ---
    for (langue, voix), liste_ws in groupes.items():
        try:
            traduction = await traduire(texte_arabe, langue)
            logger.info(f'[TRADUCTION] {langue} : "{traduction[:80]}"')

            # TTS hybride : ElevenLabs Flash ou Azure Neural selon la langue
            audio, fmt = await generer_tts(traduction, langue)

            latence = int((time.time() - debut) * 1000)

            # Enregistrer dans le monitoring
            latences.append(latence)
            historique.append({
                "heure": datetime.now(timezone.utc).isoformat(),
                "langue": langue,
                "texte_arabe": texte_arabe,
                "traduction": traduction,
                "latence_ms": latence,
            })

            # Envoyer à chaque client du groupe
            message = {
                "type": "traduction",
                "texte_arabe": texte_arabe,
                "traduction": traduction,
                "latence_ms": latence,
                "langue": langue,
            }
            if audio:
                message["audio_base64"] = base64.b64encode(audio).decode()
                message["audio_format"] = fmt
            else:
                logger.warning(f"[TTS] Pas d'audio pour {langue} — envoi texte uniquement")

            msg_json = json.dumps(message)
            for ws in liste_ws:
                try:
                    await ws.send_text(msg_json)
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"[ERREUR PIPELINE] langue={langue} — {e}")


async def boucle_pipeline(queue: asyncio.Queue):
    """Boucle infinie qui dépile les morceaux audio et lance le pipeline."""
    while True:
        audio_data = await queue.get()
        if session["active"]:
            try:
                await pipeline_traduction(audio_data)
            except Exception as e:
                logger.error(f"[ERREUR PIPELINE] {e}")


# ============================================================
# 9. WEBSOCKET FIDÈLES — /ws/listen
# ============================================================

@app.websocket("/ws/listen")
async def ws_listen(ws: WebSocket):
    """
    Le fidèle se connecte et envoie ses préférences :
    {"lang": "fr", "voice": "male"}
    Le serveur lui envoie ensuite les traductions en temps réel.
    """
    await ws.accept()
    client_id = str(id(ws))

    try:
        # Recevoir les préférences du fidèle
        data = await ws.receive_json()
        langue = data.get("lang", "fr")
        voix = data.get("voice", "male")

        if langue not in LANGUES:
            langue = "fr"
        if voix not in ("male", "female"):
            voix = "male"

        clients[client_id] = {"ws": ws, "lang": langue, "voice": voix}

        # Envoyer le statut initial au client
        await ws.send_text(json.dumps({
            "type": "status",
            "mode": "azure" if AZURE_ENABLED else "legacy",
            "session_active": session["active"],
        }))

        # Garder la connexion ouverte — le fidèle peut changer ses préférences
        while True:
            msg = await ws.receive_text()
            try:
                update = json.loads(msg)
                if "lang" in update and update["lang"] in LANGUES:
                    clients[client_id]["lang"] = update["lang"]
                if "voice" in update and update["voice"] in ("male", "female"):
                    clients[client_id]["voice"] = update["voice"]
            except json.JSONDecodeError:
                pass

    except WebSocketDisconnect:
        pass
    finally:
        clients.pop(client_id, None)


# ============================================================
# 10bis. RÉCEPTION AUDIO DEPUIS WINDOWS — POST /api/audio/chunk
#   Sur Windows, le micro ne peut pas être passé à Docker.
#   Un script externe capture le micro et envoie les morceaux ici.
# ============================================================

@app.post("/api/audio/chunk")
async def recevoir_audio_chunk(file: UploadFile = File(...)):
    """
    Reçoit un fichier audio WAV envoyé par le script de capture Windows.
    Le met dans la queue pour traitement par le pipeline.
    """
    if not session["active"]:
        logger.warning("[CHUNK] Session inactive — audio ignoré")
        raise HTTPException(
            status_code=409,
            detail="Aucune session active. Démarrez une session d'abord.",
        )

    # Lire le contenu du fichier WAV
    wav_data = await file.read()

    # Extraire les données audio brutes (sans l'en-tête WAV)
    try:
        wav_io = io.BytesIO(wav_data)
        with wave.open(wav_io, "rb") as wf:
            audio_data = wf.readframes(wf.getnframes())
    except Exception:
        raise HTTPException(status_code=400, detail="Fichier WAV invalide.")

    # Mettre dans la queue pour le pipeline
    logger.info(f"[CHUNK] Audio reçu — {len(audio_data)} bytes")
    await audio_queue.put(audio_data)

    return {"status": "ok", "taille_bytes": len(audio_data)}


# ============================================================
# 10ter. STREAMING AUDIO — WebSocket /ws/audio-stream
#   Reçoit l'audio en streaming depuis audio_capture.py
#   et le pousse dans Azure Speech Translation ou Legacy.
# ============================================================

@app.websocket("/ws/audio-stream")
async def audio_stream_websocket(websocket: WebSocket):
    """
    Reçoit l'audio en streaming depuis audio_capture.py
    et le pousse dans Azure Speech Translation (ou Legacy).
    """
    await websocket.accept()
    logger.info("[WS AUDIO] Client audio connecté")

    if AZURE_ENABLED:
        # --- Mode Azure : streaming continu ---
        translator = AzureSpeechTranslator(
            speech_key=AZURE_SPEECH_KEY,
            speech_region=AZURE_SPEECH_REGION,
            event_loop=asyncio.get_event_loop(),
        )
        translator.start()
        try:
            while True:
                data = await websocket.receive_bytes()
                translator.push_audio(data)
        except WebSocketDisconnect:
            logger.info("[WS AUDIO] Client audio déconnecté")
        except Exception as e:
            logger.error(f"[WS AUDIO] Erreur : {e}")
        finally:
            translator.stop()
    else:
        # --- Mode Legacy : accumule ~5s d'audio puis Whisper+Gemini ---
        logger.info("[WS AUDIO] Mode Legacy — accumulation par chunks de 5s")
        buffer = bytearray()
        target_size = SAMPLE_RATE * 2 * CHUNK_DURATION  # 16kHz * 2 octets * 5s = 160000 octets
        try:
            while True:
                data = await websocket.receive_bytes()
                buffer.extend(data)
                # Quand on a ~5 secondes d'audio, envoyer au pipeline Legacy
                if len(buffer) >= target_size:
                    chunk = bytes(buffer[:target_size])
                    buffer = buffer[target_size:]
                    rms = calculer_rms(chunk)
                    if rms > SILENCE_THRESHOLD:
                        logger.info(f"[WS AUDIO LEGACY] Chunk voix détecté — {len(chunk)} bytes, RMS={rms:.0f}")
                        await audio_queue.put(chunk)
                    else:
                        logger.debug(f"[WS AUDIO LEGACY] Silence — chunk ignoré (RMS={rms:.0f})")
        except WebSocketDisconnect:
            logger.info("[WS AUDIO] Client audio déconnecté")
        except Exception as e:
            logger.error(f"[WS AUDIO] Erreur : {e}")


# ============================================================
# 10. API ADMIN — Protégée par PIN (header X-Admin-Pin)
# ============================================================

def verifier_pin(request: Request):
    """Vérifie que le header X-Admin-Pin correspond au PIN configuré."""
    pin = request.headers.get("X-Admin-Pin", "")
    if pin != ADMIN_PIN:
        raise HTTPException(status_code=403, detail="PIN incorrect")


@app.post("/api/admin/session/start")
async def admin_start_session(request: Request):
    """Démarre une session de traduction."""
    verifier_pin(request)
    global last_voice_time

    # Lire les langues actives depuis le body JSON (si fourni)
    try:
        body = await request.json()
        langs = body.get("active_langs")
        if isinstance(langs, list):
            session["active_langs"] = [l for l in langs if l in LANGUES]
    except Exception:
        pass  # Pas de body JSON → on garde les langues par défaut

    session["active"] = True
    session["started_at"] = datetime.now(timezone.utc).isoformat()
    last_voice_time = time.time()
    return {"status": "session démarrée", "session": session}


@app.post("/api/admin/session/stop")
async def admin_stop_session(request: Request):
    """Arrête la session de traduction."""
    verifier_pin(request)
    session["active"] = False
    session["started_at"] = None
    return {"status": "session arrêtée", "session": session}


@app.get("/api/status")
async def get_status():
    """Statut en temps réel du système."""
    return {
        "session": session,
        "clients_connectes": len(clients),
        "derniere_latence_ms": latences[-1] if latences else None,
        "latence_moyenne_ms": (
            int(sum(latences) / len(latences)) if latences else 0
        ),
        "box_id": BOX_ID,
    }


@app.get("/api/admin/history")
async def admin_history(request: Request):
    """Historique des 50 dernières traductions."""
    verifier_pin(request)
    return {"historique": list(historique)}


@app.get("/api/health")
async def health_check():
    """Vérifie que le système et les API sont configurés."""
    return {
        "status": "ok",
        "mosque": MOSQUE_NAME,
        "box_id": BOX_ID,
        "mode": "azure" if AZURE_ENABLED else "legacy",
        "apis": {
            "azure_speech": AZURE_ENABLED,
            "openai": bool(OPENAI_API_KEY),
            "gemini": bool(GEMINI_API_KEY),
            "elevenlabs": bool(ELEVENLABS_API_KEY),
        },
    }


@app.get("/api/gemini/models")
async def list_gemini_models():
    """Liste les modèles Gemini disponibles avec la clé API."""
    try:
        modeles = await asyncio.to_thread(lambda: list(genai.list_models()))
        noms = [m.name for m in modeles if "generateContent" in (m.supported_generation_methods or [])]
        return {"count": len(noms), "models": noms[:20]}
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# 12. DÉMARRAGE — Lance tout automatiquement
# ============================================================

@app.on_event("startup")
async def on_startup():
    """Au démarrage : crée la queue audio, lance le thread micro et la boucle."""
    global audio_queue
    loop = asyncio.get_event_loop()

    # Créer la file d'attente audio dans la boucle async
    audio_queue = asyncio.Queue()

    # Lancer la capture audio dans un thread séparé
    audio_thread = threading.Thread(
        target=thread_capture_audio,
        args=(loop, audio_queue),
        daemon=True,
    )
    audio_thread.start()

    # Lancer la boucle du pipeline de traduction
    asyncio.create_task(boucle_pipeline(audio_queue))

    mode_label = "Azure Speech Translation" if AZURE_ENABLED else "Legacy (Whisper + Gemini)"
    logger.info(f"Démarré — {MOSQUE_NAME} — Pipeline: {mode_label}")
    logger.info(f"Langues actives : {', '.join(LANGUES.keys())} ({len(LANGUES)} langues)")
    logger.info("En attente de la voix de l'imam...")


# Servir le frontend (fichiers HTML/JS/CSS)
# Deux chemins possibles : ../frontend (local) ou ./frontend (Docker volume)
_backend_dir = os.path.dirname(os.path.abspath(__file__))
_candidate1 = os.path.join(_backend_dir, "..", "frontend")   # structure locale
_candidate2 = os.path.join(_backend_dir, "frontend")         # volume Docker monté dans /app/frontend
FRONTEND_DIR = None
for _path in [_candidate1, _candidate2]:
    if os.path.isdir(_path) and os.listdir(_path):
        FRONTEND_DIR = _path
        break

if FRONTEND_DIR:
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    logger.warning("Dossier frontend introuvable ou vide")
    logger.warning("L'API fonctionne, mais pas de site web servi.")


# ============================================================
# Point d'entrée — python main.py
# ============================================================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
