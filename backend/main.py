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
import threading
from datetime import datetime, timezone
from collections import deque

import pyaudio
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File
from fastapi.staticfiles import StaticFiles
import openai
import anthropic
import uvicorn


# ============================================================
# 1. CONFIGURATION — Clés API depuis le fichier .env
# ============================================================

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ADMIN_PIN = os.getenv("ADMIN_PIN", "0000")
MOSQUE_NAME = os.getenv("MOSQUE_NAME", "Mosquée")
BOX_ID = os.getenv("BOX_ID", "khutbabox-001")

openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

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

# Langues supportées (code → nom complet pour le prompt Claude)
LANGUES = {
    "fr": "français",
    "en": "anglais",
    "tr": "turc",
    "ur": "ourdou",
    "wo": "wolof",
    "ber": "berbère (kabyle)",
}

# Voix ElevenLabs (IDs par défaut — modifiables dans .env)
VOIX = {
    "male": os.getenv("ELEVENLABS_VOICE_MALE", "pNInz6obpgDQGcFmaJgB"),
    "female": os.getenv("ELEVENLABS_VOICE_FEMALE", "21m00Tcm4TlvDq8ikWAM"),
}


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
AUDIO_FORMAT = pyaudio.paInt16
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
# 3. CAPTURE AUDIO — Thread séparé (micro USB)
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
        print("[KhutbaBox] Pas de micro détecté (normal dans Docker).")
        print("[KhutbaBox] L'audio sera reçu via POST /api/audio/chunk")
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
        print(f"[ERREUR MICRO] {e}")
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()


# ============================================================
# 5. TRANSCRIPTION — OpenAI Whisper (arabe)
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
# 4. DÉTECTION CORAN — Claude Haiku
# ============================================================

async def detecter_coran(texte_arabe: str) -> bool:
    """
    Demande à Claude si le texte est une récitation coranique
    ou un discours libre. Retourne True si c'est du Coran.
    """
    response = await asyncio.to_thread(
        anthropic_client.messages.create,
        model="claude-haiku-4-5-20251001",
        max_tokens=10,
        messages=[{
            "role": "user",
            "content": (
                f"Voici un extrait transcrit d'un sermon de mosquée en arabe :\n\n"
                f"«{texte_arabe}»\n\n"
                f"Ce texte est-il une récitation coranique (verset du Coran récité) "
                f"ou un discours/sermon libre de l'imam ?\n"
                f"Réponds uniquement par un seul mot : CORAN ou DISCOURS"
            ),
        }],
    )
    return "CORAN" in response.content[0].text.upper()


# ============================================================
# 6. TRADUCTION — Claude Haiku + glossaire protégé
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

    response = await asyncio.to_thread(
        anthropic_client.messages.create,
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": (
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
            ),
        }],
    )
    return response.content[0].text


# ============================================================
# 7. SYNTHÈSE VOCALE — ElevenLabs (via httpx)
# ============================================================

async def synthetiser_voix(texte: str, voix: str = "male") -> bytes:
    """
    Envoie le texte à ElevenLabs et retourne l'audio MP3.
    voix = "male" ou "female"
    """
    voice_id = VOIX.get(voix, VOIX["male"])
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
                "voice_settings": {
                    "stability": 0.6,
                    "similarity_boost": 0.8,
                },
            },
            params={"output_format": "mp3_44100_128"},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.content


# ============================================================
# 8. PIPELINE COMPLET — Transcription → Détection → Traduction → Voix → Envoi
# ============================================================

async def pipeline_traduction(audio_data: bytes):
    """Traite un morceau audio de 5 secondes à travers tout le pipeline."""
    debut = time.time()

    # --- Étape 1 : Transcription arabe ---
    texte_arabe = await transcrire(audio_data)
    if not texte_arabe.strip():
        return

    # --- Étape 2 : Détection Coran ---
    est_coran = await detecter_coran(texte_arabe)

    if est_coran:
        # Récitation coranique → notifier tous les clients, pas de traduction
        message = json.dumps({
            "type": "coran",
            "texte_arabe": texte_arabe,
            "message": "تلاوة القرآن الكريم",
        })
        for info in list(clients.values()):
            try:
                await info["ws"].send_text(message)
            except Exception:
                pass
        return

    # --- Étape 3 : Regrouper les clients par (langue, voix) ---
    groupes: dict[tuple[str, str], list[WebSocket]] = {}
    for info in list(clients.values()):
        cle = (info["lang"], info["voice"])
        groupes.setdefault(cle, []).append(info["ws"])

    # --- Étape 4 : Traduire + synthétiser pour chaque groupe ---
    for (langue, voix), liste_ws in groupes.items():
        try:
            traduction = await traduire(texte_arabe, langue)
            audio_mp3 = await synthetiser_voix(traduction, voix)
            audio_base64 = base64.b64encode(audio_mp3).decode()

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
            message = json.dumps({
                "type": "traduction",
                "texte_arabe": texte_arabe,
                "traduction": traduction,
                "audio_base64": audio_base64,
                "latence_ms": latence,
                "langue": langue,
            })
            for ws in liste_ws:
                try:
                    await ws.send_text(message)
                except Exception:
                    pass

        except Exception as e:
            print(f"[ERREUR PIPELINE] langue={langue} — {e}")


async def boucle_pipeline(queue: asyncio.Queue):
    """Boucle infinie qui dépile les morceaux audio et lance le pipeline."""
    while True:
        audio_data = await queue.get()
        if session["active"]:
            try:
                await pipeline_traduction(audio_data)
            except Exception as e:
                print(f"[ERREUR PIPELINE] {e}")


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
    await audio_queue.put(audio_data)

    return {"status": "ok", "taille_bytes": len(audio_data)}


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
        "apis": {
            "openai": bool(OPENAI_API_KEY),
            "anthropic": bool(ANTHROPIC_API_KEY),
            "elevenlabs": bool(ELEVENLABS_API_KEY),
        },
    }


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

    print(f"[KhutbaBox] Démarré — {MOSQUE_NAME} — Mode {session['mode']}")
    print(f"[KhutbaBox] En attente de la voix de l'imam...")


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
    print(f"[KhutbaBox] ⚠ Dossier frontend introuvable ou vide")
    print(f"[KhutbaBox]   L'API fonctionne, mais pas de site web servi.")


# ============================================================
# Point d'entrée — python main.py
# ============================================================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
