# Architecture v2 "Best of Breed" — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer le pipeline Azure Speech Translation + Gemini par Speechmatics STT + GPT-4.1 mini traduction, ajouter le bouton pause Adhan, et intégrer le glossaire v2 (380 termes).

**Architecture:** Speechmatics pour le STT arabe (détection auto de langue), GPT-4.1 mini pour la traduction contextuelle avec glossaire islamique, ElevenLabs Flash v2.5 + Azure Neural TTS pour la voix. Le backend FastAPI reçoit l'audio en WebSocket streaming, le transcrit via Speechmatics, traduit via OpenAI, génère la voix, et envoie aux smartphones via WebSocket.

**Tech Stack:** Python 3, FastAPI, speechmatics-rt, openai, httpx, azure-cognitiveservices-speech (TTS uniquement), ElevenLabs API

**Spec:** `docs/superpowers/specs/2026-04-10-architecture-v2-design.md`

---

## Structure des fichiers

| Fichier | Action | Responsabilité |
|---------|--------|----------------|
| `backend/main.py` | Modifier | Backend principal — pipeline STT → traduction → TTS → WebSocket |
| `backend/glossary.json` | Remplacer | Glossaire islamique 380 termes (copie du v2) |
| `backend/requirements.txt` | Modifier | Dépendances Python |
| `frontend/index.html` | Modifier | Page fidèles — ajouter affichage pause |
| `frontend/admin.html` | Modifier | Dashboard admin — ajouter bouton Pause/Reprendre |
| `.env` | Modifier | Ajouter SPEECHMATICS_API_KEY |
| `CLAUDE.md` | Modifier | Mettre à jour la stack technique |

---

### Task 1 : Mettre à jour les dépendances

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Modifier requirements.txt**

Remplacer le contenu de `backend/requirements.txt` par :

```txt
# Serveur web
fastapi==0.115.12
uvicorn==0.34.2

# WebSocket (diffusion audio vers smartphones)
websockets==15.0.1

# Capture audio depuis le micro USB
sounddevice==0.5.1

# Speechmatics STT (reconnaissance vocale arabe, streaming temps réel)
speechmatics-rt

# Traduction LLM (GPT-4.1 mini via OpenAI API)
openai==1.78.1

# Synthèse vocale ElevenLabs (appels API HTTP)
httpx==0.28.1

# Réception de fichiers uploadés (nécessaire pour FastAPI UploadFile)
python-multipart==0.0.20

# Charger les clés API depuis le fichier .env
python-dotenv==1.1.0

# Azure Speech SDK (TTS uniquement — 3 langues : ur, bs, sq)
azure-cognitiveservices-speech>=1.40.0
```

- [ ] **Step 2: Installer les dépendances**

Run: `py -m pip install speechmatics-rt`
Expected: Installation réussie

- [ ] **Step 3: Commit**

```bash
git add backend/requirements.txt
git commit -m "deps: remplacer google-generativeai par speechmatics-rt"
```

---

### Task 2 : Copier le glossaire v2 dans le projet

**Files:**
- Replace: `backend/glossary.json`

- [ ] **Step 1: Copier le fichier glossaire**

Copier `C:\Users\bensa\Downloads\khutbabox_glossary_v2 (1).json` vers `backend/glossary.json`.

- [ ] **Step 2: Vérifier que le fichier est valide**

Run: `py -c "import json; data=json.load(open('backend/glossary.json','r',encoding='utf-8')); print(f'{len(data)} termes chargés')"`
Expected: `380 termes chargés`

- [ ] **Step 3: Commit**

```bash
git add backend/glossary.json
git commit -m "data: glossaire islamique v2 — 380 termes, 8 langues, 17 catégories"
```

---

### Task 3 : Refactorer la configuration dans main.py

**Files:**
- Modify: `backend/main.py` (section 1 — lignes 1-75)

- [ ] **Step 1: Remplacer les imports et la configuration**

Remplacer la section imports + configuration (lignes 1-75) par :

```python
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
from pathlib import Path

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
import uvicorn

# Logger principal
logger = logging.getLogger("khutbabox")
logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")


# ============================================================
# 1. CONFIGURATION — Clés API depuis le fichier .env
# ============================================================

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ADMIN_PIN = os.getenv("ADMIN_PIN", "0000")
MOSQUE_NAME = os.getenv("MOSQUE_NAME", "Mosquée")
BOX_ID = os.getenv("BOX_ID", "khutbabox-001")

# Speechmatics STT (reconnaissance vocale arabe)
SPEECHMATICS_API_KEY = os.getenv("SPEECHMATICS_API_KEY", "")

# Azure Speech — utilisé UNIQUEMENT pour le TTS (ur, bs, sq)
AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "westeurope")
AZURE_TTS_ENABLED = bool(AZURE_SPEECH_KEY) and AZURE_AVAILABLE

if AZURE_TTS_ENABLED:
    logger.info("Azure Neural TTS activé (ur, bs, sq)")
if SPEECHMATICS_API_KEY:
    logger.info("Speechmatics STT activé")
else:
    logger.warning("SPEECHMATICS_API_KEY non configurée — STT désactivé")
if OPENAI_API_KEY:
    logger.info("GPT-4.1 mini traduction activé")
else:
    logger.warning("OPENAI_API_KEY non configurée — traduction désactivée")

openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI(title="KhutbaBox", version="2.0.0")
```

Ce qui change :
- Supprimé : `import pyaudio`, `import google.generativeai`, `GEMINI_API_KEY`
- Ajouté : `SPEECHMATICS_API_KEY`
- Renommé : `AZURE_ENABLED` → `AZURE_TTS_ENABLED` (Azure n'est plus utilisé pour le STT)
- Version : 1.0.0 → 2.0.0

- [ ] **Step 2: Vérifier que le fichier se charge sans erreur**

Run: `py -c "import ast; ast.parse(open('backend/main.py','r',encoding='utf-8').read()); print('Syntaxe OK')"`
Expected: `Syntaxe OK`

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "refactor: configuration v2 — Speechmatics + GPT-4.1 mini, suppression Gemini"
```

---

### Task 4 : Charger le glossaire au démarrage et créer le prompt de traduction

**Files:**
- Modify: `backend/main.py` (section glossaire + fonction traduire)

- [ ] **Step 1: Remplacer le glossaire hardcodé par le chargement du fichier JSON**

Remplacer la section 2 (GLOSSAIRE ISLAMIQUE, lignes ~78-119) par :

```python
# ============================================================
# 2. GLOSSAIRE ISLAMIQUE — Chargé depuis glossary.json (380 termes)
# ============================================================

GLOSSARY_PATH = Path(__file__).parent / "glossary.json"

def charger_glossaire() -> list[dict]:
    """Charge le glossaire islamique depuis le fichier JSON."""
    try:
        with open(GLOSSARY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info(f"Glossaire chargé : {len(data)} termes")
        return data
    except FileNotFoundError:
        logger.warning("glossary.json introuvable — glossaire vide")
        return []

GLOSSAIRE = charger_glossaire()

# Extraire les termes critiques pour le prompt (formules rituelles + noms d'Allah)
CATEGORIES_PROMPT = {"Formules rituelles", "Noms et Attributs d'Allah", "Piliers de l'Islam", "Invocations (Du'a)"}
TERMES_PROMPT = [t for t in GLOSSAIRE if t.get("category") in CATEGORIES_PROMPT]

def construire_glossaire_prompt(langue_cible: str) -> str:
    """Construit la section glossaire du prompt pour une langue cible."""
    lignes = []
    for t in TERMES_PROMPT:
        trad = t.get(langue_cible, t.get("transliteration", ""))
        if trad:
            lignes.append(f"  - {t['arabic']} → {trad}")
    return "\n".join(lignes)
```

- [ ] **Step 2: Remplacer la fonction traduire (Gemini → GPT-4.1 mini)**

Remplacer la section 6 (TRADUCTION LEGACY, lignes ~502-534) par :

```python
# ============================================================
# 6. TRADUCTION — GPT-4.1 mini + glossaire islamique
# ============================================================

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

async def traduire(texte_source: str, langue_cible: str, langue_source: str = "arabe") -> str:
    """
    Traduit le texte dans la langue demandée via GPT-4.1 mini.
    Le glossaire islamique est injecté dans le prompt.
    """
    glossaire_str = construire_glossaire_prompt(langue_cible)
    nom_langue = LANGUES.get(langue_cible, langue_cible)

    prompt_systeme = (
        "Tu es un traducteur professionnel spécialisé dans les sermons "
        "islamiques (khoutba du vendredi).\n\n"
        "GLOSSAIRE OBLIGATOIRE — ces termes ne sont JAMAIS traduits librement :\n"
        f"{glossaire_str}\n\n"
        "RÈGLES :\n"
        "1. Respecte le ton religieux et solennel\n"
        "2. Garde les termes du glossaire tels quels\n"
        "3. Traduis le SENS, pas mot à mot\n"
        "4. Adapte les expressions idiomatiques à la langue cible\n"
        "5. Réponds uniquement avec la traduction, sans commentaire"
    )

    prompt_user = (
        f"Langue source : {langue_source}\n"
        f"Traduis en {nom_langue} :\n"
        f"«{texte_source}»"
    )

    try:
        response = await asyncio.to_thread(
            openai_client.chat.completions.create,
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": prompt_systeme},
                {"role": "user", "content": prompt_user},
            ],
            temperature=0.3,
            max_tokens=500,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"[TRADUCTION] Erreur GPT {langue_cible} : {e}")
        return ""
```

- [ ] **Step 3: Vérifier la syntaxe**

Run: `py -c "import ast; ast.parse(open('backend/main.py','r',encoding='utf-8').read()); print('Syntaxe OK')"`
Expected: `Syntaxe OK`

- [ ] **Step 4: Commit**

```bash
git add backend/main.py
git commit -m "feat: traduction GPT-4.1 mini avec glossaire islamique 380 termes"
```

---

### Task 5 : Remplacer Azure Speech Translation par Speechmatics STT

**Files:**
- Modify: `backend/main.py` (section 3b — AzureSpeechTranslator → SpeechmaticsTranscriber)

- [ ] **Step 1: Remplacer la classe AzureSpeechTranslator par SpeechmaticsTranscriber**

Remplacer toute la section 3b (class AzureSpeechTranslator, lignes ~256-393) par :

```python
# ============================================================
# 3b. SPEECHMATICS STT — Reconnaissance vocale streaming
# ============================================================

from speechmatics.rt import AsyncClient, TranscriptionConfig, AudioFormat, AudioEncoding
from speechmatics.models import ServerMessageType

class SpeechmaticsTranscriber:
    """
    Gère le pipeline Speechmatics STT streaming.
    Reçoit l'audio PCM 16kHz → renvoie le texte transcrit.
    Quand une phrase complète est reconnue, lance la traduction + TTS.
    """

    def __init__(self, api_key: str, event_loop: asyncio.AbstractEventLoop):
        self.api_key = api_key
        self.loop = event_loop
        self.client = None
        self._audio_queue = asyncio.Queue()
        self._running = False

    async def start(self):
        """Démarre la session Speechmatics en streaming."""
        self.client = AsyncClient(api_key=self.api_key)

        # Enregistrer les callbacks
        @self.client.on(ServerMessageType.ADD_PARTIAL_TRANSCRIPT)
        def on_partial(message):
            """Texte partiel — envoie aux smartphones."""
            transcript = message.get("metadata", {}).get("transcript", "")
            language = message.get("metadata", {}).get("language", "ar")
            if transcript.strip():
                asyncio.run_coroutine_threadsafe(
                    self._handle_partial(transcript, language),
                    self.loop,
                )

        @self.client.on(ServerMessageType.ADD_TRANSCRIPT)
        def on_final(message):
            """Phrase complète — lance traduction + TTS."""
            transcript = message.get("metadata", {}).get("transcript", "")
            language = message.get("metadata", {}).get("language", "ar")
            if transcript.strip():
                asyncio.run_coroutine_threadsafe(
                    self._handle_final(transcript, language),
                    self.loop,
                )

        # Démarrer la session
        await self.client.start_session(
            transcription_config=TranscriptionConfig(
                language="auto",
                enable_partials=True,
                max_delay=2,
            ),
            audio_format=AudioFormat(
                encoding=AudioEncoding.PCM_S16LE,
                sample_rate=16000,
            ),
        )
        self._running = True
        logger.info("Speechmatics STT démarré (langue: auto, partials: activés)")

    async def push_audio(self, audio_data: bytes):
        """Pousse des chunks audio PCM 16kHz 16-bit mono."""
        if self.client and self._running:
            await self.client.send_audio(audio_data)

    async def stop(self):
        """Arrête la session."""
        self._running = False
        if self.client:
            await self.client.end_session()
            self.client = None
        logger.info("Speechmatics STT arrêté")

    async def _handle_partial(self, transcript: str, detected_lang: str):
        """Envoie le texte partiel aux smartphones (texte brut, pas de traduction)."""
        logger.debug(f"[STT PARTIEL] ({detected_lang}) {transcript[:80]}")
        # Envoyer le texte partiel en broadcast (pour ceux qui lisent la langue source)
        for info in list(clients.values()):
            try:
                await info["ws"].send_text(json.dumps({
                    "type": "partial",
                    "lang": detected_lang,
                    "text": transcript,
                }))
            except Exception:
                pass

    async def _handle_final(self, transcript: str, detected_lang: str):
        """Phrase complète : traduit dans toutes les langues + TTS."""
        logger.info(f'[STT FINAL] ({detected_lang}) "{transcript[:80]}"')

        if session.get("paused"):
            logger.debug("[STT] Session en pause — traduction ignorée")
            return

        # Déterminer la langue source pour le prompt
        lang_map = {"ar": "arabe", "fr": "français", "en": "anglais"}
        langue_source = lang_map.get(detected_lang, detected_lang)

        # Détecter les langues où au moins 1 fidèle est connecté
        langues_connectees = {info["lang"] for info in clients.values()}

        # Langues à traduire = langues connectées MOINS la langue source
        langues_cibles = [l for l in langues_connectees if l != detected_lang]

        if not langues_cibles:
            logger.debug("[STT] Aucune langue cible connectée")

        debut = time.time()

        async def traiter_langue(lang: str):
            """Traduit + TTS + envoi pour une langue."""
            # Traduire
            traduction = await traduire(transcript, lang, langue_source)
            if not traduction:
                return

            # Envoyer le texte final
            await broadcast_translation("final_text", lang, text=traduction)

            # TTS seulement si des fidèles écoutent cette langue
            audio, fmt = await generer_tts(traduction, lang)
            if audio:
                audio_b64 = base64.b64encode(audio).decode()
                await broadcast_translation("audio_chunk", lang, audio_b64=audio_b64, audio_format=fmt)

        # Lancer toutes les traductions en parallèle
        await asyncio.gather(*[traiter_langue(l) for l in langues_cibles])

        latence = int((time.time() - debut) * 1000)
        latences.append(latence)
        historique.append({
            "heure": datetime.now(timezone.utc).isoformat(),
            "texte_source": transcript,
            "langue_source": detected_lang,
            "langues_cibles": langues_cibles,
            "latence_ms": latence,
            "mode": "speechmatics+gpt",
        })
        logger.info(f"[PIPELINE] {len(langues_cibles)} langues en {latence}ms")
```

- [ ] **Step 2: Vérifier la syntaxe**

Run: `py -c "import ast; ast.parse(open('backend/main.py','r',encoding='utf-8').read()); print('Syntaxe OK')"`
Expected: `Syntaxe OK`

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "feat: Speechmatics STT streaming — remplace Azure Speech Translation"
```

---

### Task 6 : Mettre à jour le WebSocket audio-stream pour Speechmatics

**Files:**
- Modify: `backend/main.py` (section 10ter — websocket audio-stream)

- [ ] **Step 1: Remplacer le handler WebSocket audio-stream**

Remplacer la section `@app.websocket("/ws/audio-stream")` (lignes ~842-891) par :

```python
# ============================================================
# 10ter. STREAMING AUDIO — WebSocket /ws/audio-stream
#   Reçoit l'audio en streaming depuis audio_capture.py
#   et le pousse dans Speechmatics STT.
# ============================================================

@app.websocket("/ws/audio-stream")
async def audio_stream_websocket(websocket: WebSocket):
    """
    Reçoit l'audio en streaming depuis audio_capture.py
    et le pousse dans Speechmatics STT.
    """
    await websocket.accept()
    logger.info("[WS AUDIO] Client audio connecté")

    if not SPEECHMATICS_API_KEY:
        logger.error("[WS AUDIO] SPEECHMATICS_API_KEY non configurée")
        await websocket.close(code=1011, reason="STT non configuré")
        return

    transcriber = SpeechmaticsTranscriber(
        api_key=SPEECHMATICS_API_KEY,
        event_loop=asyncio.get_event_loop(),
    )

    try:
        await transcriber.start()
        while True:
            data = await websocket.receive_bytes()
            await transcriber.push_audio(data)
    except WebSocketDisconnect:
        logger.info("[WS AUDIO] Client audio déconnecté")
    except Exception as e:
        logger.error(f"[WS AUDIO] Erreur : {e}")
    finally:
        await transcriber.stop()
```

- [ ] **Step 2: Vérifier la syntaxe**

Run: `py -c "import ast; ast.parse(open('backend/main.py','r',encoding='utf-8').read()); print('Syntaxe OK')"`
Expected: `Syntaxe OK`

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "feat: WebSocket audio-stream utilise Speechmatics au lieu d'Azure"
```

---

### Task 7 : Ajouter les endpoints Pause / Resume

**Files:**
- Modify: `backend/main.py` (section API admin)

- [ ] **Step 1: Ajouter le champ `paused` dans l'état session**

Dans la section état global (session dict), ajouter :

```python
session = {
    "active": False,
    "paused": False,      # ← NOUVEAU : pause Adhan/prière
    "started_at": None,
    "active_langs": list(LANGUES.keys()),
    "mosque_name": MOSQUE_NAME,
    "mode": "speechmatics+gpt",
}
```

- [ ] **Step 2: Ajouter les endpoints pause/resume**

Ajouter après les endpoints admin existants :

```python
@app.post("/api/session/pause")
async def pause_session(request: Request):
    """Met en pause la traduction (Adhan, prière, récitation)."""
    verifier_pin(request)
    session["paused"] = True
    # Notifier tous les smartphones
    for info in list(clients.values()):
        try:
            await info["ws"].send_text(json.dumps({
                "type": "status",
                "status": "paused",
                "message": "Récitation en cours — traduction en pause",
            }))
        except Exception:
            pass
    logger.info("[PAUSE] Traduction en pause")
    return {"status": "paused"}


@app.post("/api/session/resume")
async def resume_session(request: Request):
    """Reprend la traduction après la pause."""
    verifier_pin(request)
    session["paused"] = False
    # Notifier tous les smartphones
    for info in list(clients.values()):
        try:
            await info["ws"].send_text(json.dumps({
                "type": "status",
                "status": "resumed",
                "message": "",
            }))
        except Exception:
            pass
    logger.info("[RESUME] Traduction reprise")
    return {"status": "resumed"}
```

- [ ] **Step 3: Mettre à jour le endpoint /api/status**

Ajouter `"paused"` dans la réponse du endpoint `/api/status` :

```python
@app.get("/api/status")
async def get_status():
    """Statut en temps réel du système."""
    # Compter les fidèles par langue
    langues_count = {}
    for info in clients.values():
        lang = info["lang"]
        langues_count[lang] = langues_count.get(lang, 0) + 1

    return {
        "session": session,
        "clients_connectes": len(clients),
        "langues": langues_count,
        "derniere_latence_ms": latences[-1] if latences else None,
        "latence_moyenne_ms": (
            int(sum(latences) / len(latences)) if latences else 0
        ),
        "box_id": BOX_ID,
    }
```

- [ ] **Step 4: Vérifier la syntaxe**

Run: `py -c "import ast; ast.parse(open('backend/main.py','r',encoding='utf-8').read()); print('Syntaxe OK')"`
Expected: `Syntaxe OK`

- [ ] **Step 5: Commit**

```bash
git add backend/main.py
git commit -m "feat: endpoints pause/resume pour Adhan et prière"
```

---

### Task 8 : Nettoyer le code obsolète dans main.py

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: Supprimer le code obsolète**

Supprimer ces sections :
1. Le `import google.generativeai as genai` et `gemini_model` (déjà fait en Task 3)
2. La fonction `transcrire()` (Whisper legacy — lignes ~486-498)
3. La fonction `pipeline_traduction()` (pipeline legacy — lignes ~673-734)
4. La fonction `boucle_pipeline()` (lignes ~736-744)
5. La fonction `thread_capture_audio()` (capture micro local PyAudio — lignes ~400-479)
6. La classe `AzureSpeechTranslator` (déjà remplacée en Task 5)
7. Le endpoint `/api/gemini/models` (lignes ~973-981)
8. Les variables `HAS_PYAUDIO`, `AUDIO_FORMAT`, `FRAMES_PER_READ`
9. Le import `pyaudio`

Garder :
- Le TTS Azure (`azure_tts()`) — utilisé pour ur/bs/sq
- Le TTS ElevenLabs (`elevenlabs_tts_stream()`) — utilisé pour 5 langues
- Le routeur TTS (`generer_tts()`)
- Le broadcast WebSocket (`broadcast_translation()`)
- Tous les endpoints admin
- Le WebSocket fidèles (`/ws/listen`)
- Le endpoint `/api/audio/chunk` (POST legacy — peut servir pour tests)

- [ ] **Step 2: Mettre à jour on_startup**

Remplacer la fonction `on_startup` pour retirer les références au thread micro et à la boucle pipeline :

```python
@app.on_event("startup")
async def on_startup():
    """Au démarrage : initialise les sémaphores."""
    global elevenlabs_semaphore
    elevenlabs_semaphore = asyncio.Semaphore(2)
    logger.info(f"KhutbaBox v2 démarré — {MOSQUE_NAME}")
    logger.info(f"STT: Speechmatics | Traduction: GPT-4.1 mini | TTS: ElevenLabs + Azure")
    logger.info(f"Glossaire: {len(GLOSSAIRE)} termes chargés")
```

- [ ] **Step 3: Mettre à jour /api/health**

```python
@app.get("/api/health")
async def health_check():
    """Vérifie que le système et les API sont configurés."""
    return {
        "status": "ok",
        "version": "2.0.0",
        "mosque": MOSQUE_NAME,
        "box_id": BOX_ID,
        "apis": {
            "speechmatics_stt": bool(SPEECHMATICS_API_KEY),
            "openai_gpt": bool(OPENAI_API_KEY),
            "elevenlabs_tts": bool(ELEVENLABS_API_KEY),
            "azure_tts": AZURE_TTS_ENABLED,
        },
        "glossaire_termes": len(GLOSSAIRE),
    }
```

- [ ] **Step 4: Vérifier la syntaxe**

Run: `py -c "import ast; ast.parse(open('backend/main.py','r',encoding='utf-8').read()); print('Syntaxe OK')"`
Expected: `Syntaxe OK`

- [ ] **Step 5: Commit**

```bash
git add backend/main.py
git commit -m "cleanup: suppression code legacy (Whisper, Gemini, PyAudio, Azure STT)"
```

---

### Task 9 : Ajouter le bouton Pause dans le dashboard admin

**Files:**
- Modify: `frontend/admin.html`

- [ ] **Step 1: Ajouter les boutons Pause/Reprendre dans le HTML**

Ajouter dans la section dashboard (après les stats existantes) :

```html
<!-- Bouton Pause / Reprendre -->
<div class="carte" id="carte-pause">
  <div class="carte-titre">Contrôle Traduction</div>
  <div style="display:flex; gap:12px; margin-top:12px;">
    <button id="btn-pause" class="btn btn-danger" onclick="pauseTraduction()">
      Pause (Adhan / Prière)
    </button>
    <button id="btn-resume" class="btn btn-success" onclick="resumeTraduction()" style="display:none;">
      Reprendre la traduction
    </button>
  </div>
  <div id="pause-status" style="margin-top:8px; color:#c9a84c; display:none;">
    Récitation en cours — traduction en pause
  </div>
</div>
```

- [ ] **Step 2: Ajouter le CSS pour les boutons**

```css
.btn-danger {
  background: #d32f2f;
  color: white;
  border: none;
  padding: 12px 24px;
  border-radius: 8px;
  font-size: 16px;
  cursor: pointer;
}
.btn-danger:hover { background: #b71c1c; }
.btn-success {
  background: #2e7d32;
  color: white;
  border: none;
  padding: 12px 24px;
  border-radius: 8px;
  font-size: 16px;
  cursor: pointer;
}
.btn-success:hover { background: #1b5e20; }
```

- [ ] **Step 3: Ajouter le JavaScript pour les boutons**

```javascript
async function pauseTraduction() {
  const res = await fetch('/api/session/pause', {
    method: 'POST',
    headers: { 'X-Admin-Pin': pin },
  });
  if (res.ok) {
    document.getElementById('btn-pause').style.display = 'none';
    document.getElementById('btn-resume').style.display = 'inline-block';
    document.getElementById('pause-status').style.display = 'block';
  }
}

async function resumeTraduction() {
  const res = await fetch('/api/session/resume', {
    method: 'POST',
    headers: { 'X-Admin-Pin': pin },
  });
  if (res.ok) {
    document.getElementById('btn-pause').style.display = 'inline-block';
    document.getElementById('btn-resume').style.display = 'none';
    document.getElementById('pause-status').style.display = 'none';
  }
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/admin.html
git commit -m "feat: bouton Pause/Reprendre dans le dashboard admin"
```

---

### Task 10 : Afficher "Récitation en cours" sur la page fidèles

**Files:**
- Modify: `frontend/index.html`

- [ ] **Step 1: Ajouter le bandeau de pause dans le HTML**

Ajouter après le header, avant la zone de texte :

```html
<!-- Bandeau pause (Adhan/prière) -->
<div id="bandeau-pause" style="display:none; text-align:center; padding:20px; background:rgba(201,168,76,0.15); border:1px solid rgba(201,168,76,0.3); border-radius:12px; margin:16px 20px;">
  <div style="font-size:28px; margin-bottom:8px;">🕌</div>
  <div style="color:#c9a84c; font-size:18px; font-weight:500;">Récitation en cours</div>
  <div style="color:#a0998a; font-size:14px; margin-top:4px;">Traduction en pause</div>
</div>
```

- [ ] **Step 2: Gérer le message status dans le JavaScript WebSocket**

Dans le handler `ws.onmessage`, ajouter la gestion du type "status" :

```javascript
// Dans le handler onmessage existant, ajouter ce cas :
if (data.type === 'status') {
  const bandeau = document.getElementById('bandeau-pause');
  if (data.status === 'paused') {
    bandeau.style.display = 'block';
  } else if (data.status === 'resumed') {
    bandeau.style.display = 'none';
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/index.html
git commit -m "feat: bandeau 'Récitation en cours' sur page fidèles pendant pause"
```

---

### Task 11 : Mettre à jour .env et CLAUDE.md

**Files:**
- Modify: `.env` (template)
- Modify: `CLAUDE.md`

- [ ] **Step 1: Mettre à jour le fichier .env**

Ajouter `SPEECHMATICS_API_KEY` et retirer `GEMINI_API_KEY` :

```env
# Speechmatics STT (reconnaissance vocale arabe)
SPEECHMATICS_API_KEY=your_speechmatics_api_key_here

# OpenAI (traduction GPT-4.1 mini)
OPENAI_API_KEY=your_openai_api_key_here

# ElevenLabs (TTS voix 5 langues)
ELEVENLABS_API_KEY=your_elevenlabs_api_key_here

# Azure Speech (TTS uniquement — 3 langues : ur, bs, sq)
AZURE_SPEECH_KEY=your_azure_speech_key_here
AZURE_SPEECH_REGION=westeurope

# Admin
ADMIN_PIN=0000
MOSQUE_NAME=Mosquée
BOX_ID=khutbabox-001
```

- [ ] **Step 2: Mettre à jour CLAUDE.md — section Stack technique**

Remplacer la section "Stack technique" par :

```markdown
## Stack technique
- Backend : Python + FastAPI (backend/main.py)
- Frontend : HTML/JS (frontend/index.html et admin.html)
- Capture audio : sounddevice (Windows) / WebSocket streaming ~100ms
- IA transcription : Speechmatics STT (arabe tous dialectes, détection auto de langue)
- IA traduction : OpenAI GPT-4.1 mini (avec glossaire islamique 380 termes)
- IA voix : ElevenLabs Flash v2.5 (5 langues) + Azure Neural TTS (3 langues)
- Infrastructure : Docker Compose + VPS
```

Mettre à jour la section "Clés API nécessaires" :

```markdown
## Clés API nécessaires (dans le fichier .env)
- SPEECHMATICS_API_KEY : pour Speechmatics STT (reconnaissance vocale arabe)
- OPENAI_API_KEY : pour GPT-4.1 mini (traduction 8 langues)
- ELEVENLABS_API_KEY : pour ElevenLabs Flash v2.5 (TTS 5 langues)
- AZURE_SPEECH_KEY : pour Azure Neural TTS (TTS 3 langues : ur, bs, sq)
- AZURE_SPEECH_REGION : westeurope
- ADMIN_PIN : code secret dashboard admin
- MOSQUE_NAME : nom de la mosquée
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: mise à jour CLAUDE.md pour architecture v2"
```

---

### Task 12 : Test d'intégration complet

**Files:**
- Aucun fichier modifié — test uniquement

- [ ] **Step 1: Vérifier que le backend démarre sans erreur**

Run: `cd backend && py -c "from main import app; print('Import OK')"`
Expected: `Import OK` (avec logs de chargement glossaire)

- [ ] **Step 2: Tester le chargement du glossaire**

Run: `py -c "from backend.main import GLOSSAIRE, TERMES_PROMPT; print(f'Glossaire: {len(GLOSSAIRE)} termes, Prompt: {len(TERMES_PROMPT)} termes')"`
Expected: `Glossaire: 380 termes, Prompt: ~130 termes` (environ)

- [ ] **Step 3: Tester le prompt de traduction**

Run: `py -c "from backend.main import construire_glossaire_prompt; print(construire_glossaire_prompt('fr')[:200])"`
Expected: Affiche les premières lignes du glossaire en français

- [ ] **Step 4: Démarrer le serveur et vérifier /api/health**

Run: `cd backend && py -m uvicorn main:app --host 0.0.0.0 --port 8000 &`
Puis: `curl http://localhost:8000/api/health`
Expected:
```json
{
  "status": "ok",
  "version": "2.0.0",
  "apis": {
    "speechmatics_stt": true/false,
    "openai_gpt": true/false,
    "elevenlabs_tts": true/false,
    "azure_tts": true/false
  },
  "glossaire_termes": 380
}
```

- [ ] **Step 5: Commit final**

```bash
git add -A
git commit -m "v2.0.0: Architecture Best of Breed — Speechmatics + GPT-4.1 mini + glossaire 380 termes"
```
