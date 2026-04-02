# KhutbaBox — Guide pour Claude Code
> Dernière mise à jour : 2026-04-02

## C'est quoi ce projet
Système de traduction en temps réel des sermons de mosquée.
Le boîtier capte le micro de l'imam, traduit par IA, et envoie
la traduction vocale sur les smartphones des fidèles via WiFi.
Projet développé par Boualem — IA Factory — Genève.

## Je suis débutant — règles de communication
- Toujours expliquer en français simple, jamais de jargon sans explication
- Toujours une seule étape à la fois
- Avant de modifier un fichier, me dire ce que tu vas changer et pourquoi
- Si quelque chose peut casser, me prévenir avant
- Quand il y a une erreur, expliquer la cause simplement

## Règles importantes
- Avant chaque tâche de code, présente d'abord un plan détaillé de ce que tu vas faire étape par étape. Attends ma validation avant de commencer à écrire le moindre code.
- Si la tâche touche plusieurs fichiers, liste exactement quels fichiers tu vas modifier et pourquoi avant de commencer.

## Stack technique
- Backend : Python + FastAPI (backend/main.py)
- Frontend : HTML/JS (frontend/index.html et admin.html)
- Capture audio : sounddevice (Windows) / PyAudio (Linux/Docker), streaming WebSocket ~100ms
- IA transcription + traduction : Azure Speech Translation SDK (STT arabe + traduction simultanée, streaming)
- IA voix : ElevenLabs Flash v2.5 (5 langues) + Azure Neural TTS (3 langues)
- Fallback : OpenAI Whisper (STT) + Google Gemini Flash (traduction) — gardés jusqu'à validation Azure
- Infrastructure : Docker Compose

## Clés API nécessaires (dans le fichier .env)
- AZURE_SPEECH_KEY : pour Azure Speech Translation (STT + traduction + TTS 3 langues)
- AZURE_SPEECH_REGION : westeurope
- ELEVENLABS_API_KEY : pour ElevenLabs Flash v2.5 (TTS 5 langues)
- OPENAI_API_KEY : pour Whisper (fallback STT uniquement)
- GEMINI_API_KEY : pour Gemini (fallback traduction uniquement)
- ADMIN_PIN : code secret dashboard admin
- MOSQUE_NAME : nom de la mosquée

## Structure des fichiers
- backend/main.py : cerveau du système, tout le pipeline IA
- backend/requirements.txt : liste des outils Python
- backend/Dockerfile : recette pour construire le backend
- frontend/index.html : page smartphone des fidèles
- frontend/admin.html : dashboard responsable mosquée
- display/lcd_status.py : affichage écran sur le boîtier physique
- scripts/setup.sh : installation automatique Raspberry Pi
- docker-compose.yml : lance tout le projet
- .env : clés secrètes (ne jamais partager ce fichier)

## Roadmap de construction — ordre exact

### Déjà fait ✅
- Structure des dossiers créée
- CLAUDE.md créé et mis à jour
- backend/main.py écrit et corrigé
- backend/requirements.txt rempli (sounddevice remplace pyaudio sur Windows)
- backend/Dockerfile écrit
- docker-compose.yml écrit
- frontend/index.html écrit (page PWA fidèles)
- frontend/admin.html écrit (dashboard admin)
- scripts/audio_capture.py écrit (capture micro avec sounddevice)
- display/lcd_status.py écrit
- scripts/setup.sh écrit
- Docker tourne sur http://localhost

### À faire dans cet ordre exact

ÉTAPE 1 — Tester le MVP complet (prochaine étape)
Lancer docker compose up --build
Ouvrir http://localhost sur le navigateur
Vérifier que les pages fidèles et admin s'affichent.
Tester le script audio_capture.py sur Windows.

ÉTAPE 2 — Mettre les vraies clés API dans .env
Remplacer les fausses clés par les vraies clés API :
Azure Speech, ElevenLabs, OpenAI (fallback), Gemini (fallback).

ÉTAPE 3 — Test audio complet
Simuler un vrai sermon avec audio arabe.
Vérifier traduction streaming et voix sur les 8 langues.

ÉTAPE 4 — Corrections
Corriger ce qui ne marche pas.

ÉTAPE 5 — Transfert Raspberry Pi
Copier le projet sur le Raspberry Pi.
Lancer le script setup.sh

### Règle absolue
Toujours suivre cet ordre sans sauter d'étape.
Valider que chaque étape fonctionne avant
de passer à la suivante.

## Migration v2 — Architecture Pro

### Nouveau pipeline audio (remplace l'ancien)
```
Micro → WebSocket streaming (chunks ~100ms, PCM 16kHz 16-bit mono)
    → Azure Speech Translation SDK (STT arabe + traduction simultanée)
        → recognizing (texte partiel → WebSocket smartphones)
        → recognized (phrase complète → TTS → WebSocket smartphones)
```
- Latence cible : < 2 secondes (ancien pipeline : 5-6 secondes)
- Azure Speech Translation fait STT + traduction en un seul appel streaming
- SDK Python : `azure-cognitiveservices-speech` (>=1.40.0)
- Région Azure : westeurope
- Fallback : Whisper + Gemini maintenus jusqu'à validation complète d'Azure

### Les 8 langues cibles

| # | Langue | Code | Code Azure target | TTS Provider |
|---|--------|------|--------------------|--------------|
| 1 | Français | fr | fr | ElevenLabs Flash v2.5 |
| 2 | English | en | en | ElevenLabs Flash v2.5 |
| 3 | Español | es | es | ElevenLabs Flash v2.5 |
| 4 | Português | pt | pt | ElevenLabs Flash v2.5 |
| 5 | Türkçe | tr | tr | ElevenLabs Flash v2.5 |
| 6 | Urdu | ur | ur | Azure Neural TTS (ur-PK) |
| 7 | Bosanski | bs | bs | Azure Neural TTS (bs-BA) |
| 8 | Shqip (Albanais) | sq | sq | Azure Neural TTS (sq-AL) |

### Stratégie TTS hybride
- 5 langues via ElevenLabs Flash v2.5 (model_id: `eleven_flash_v2_5`) → ~75ms latence
- 3 langues via Azure Neural TTS (inclus dans l'abonnement Azure Speech) → ~200ms latence
- Albanais n'est supporté par aucun modèle ElevenLabs → Azure Neural TTS obligatoire

### Détection Coran / Adhan — SUPPRIMÉE du MVP
La détection Coran et Adhan est **supprimée pour le MVP**. Tout audio capté est traduit sans filtre.

Fonctionnalités reportées à v2 future :
- Détection intelligente Coran + Adhan
- Suspension automatique de la traduction pendant les récitations
- Affichage spécial "Récitation du Coran en cours" sur les smartphones

### Capture audio — streaming WebSocket
- WebSocket persistant `ws://backend/ws/audio-stream`
- Micro-frames de ~100ms en PCM 16kHz 16-bit mono
- Le script `audio_capture.py` garde la détection silence (RMS threshold) mais envoie en continu
- Remplace l'ancien système de chunks fixes de 5 secondes

### Protocole WebSocket smartphones — 3 types de messages
```json
{ "type": "partial", "lang": "fr", "text": "texte partiel..." }
{ "type": "final_text", "lang": "fr", "text": "phrase complète" }
{ "type": "audio_chunk", "lang": "fr", "data": "base64..." }
```

### Dépendances à ajouter (requirements.txt)
```
azure-cognitiveservices-speech>=1.40.0
```

### Coûts mensuels par mosquée
- Azure Speech Translation : ~$12/mois
- ElevenLabs Flash v2.5 (5 langues) : ~$15/mois
- Azure Neural TTS (3 langues) : ~$5/mois
- Serveur VPS : ~$10/mois
- **Total : ~$42/mois**
- Prix vente SaaS Pro 8 langues : 349 CHF/mois
- Marge : ~88%

### Ordre de migration — 4 phases

**Phase 1** : Azure Speech Translation backend (remplace Whisper + Gemini traduction)
**Phase 2** : Capture audio streaming (WebSocket remplace POST chunks)
**Phase 3** : TTS hybride ElevenLabs Flash + Azure Neural (remplace ElevenLabs classique)
**Phase 4** : Frontend PWA (nouveau protocole WebSocket partial/final/audio_chunk)

Chaque phase est testable indépendamment. Fallback Whisper+Gemini maintenu jusqu'à validation Azure.

**Phases futures (v2, hors MVP) :**
- Détection Coran / Adhan avec suspension traduction
- Dashboard admin avancé (seuil détection, stats latence)
