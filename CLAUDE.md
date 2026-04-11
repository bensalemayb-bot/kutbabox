# KhutbaBox — Guide pour Claude Code
> Dernière mise à jour : 2026-04-11

## C'est quoi ce projet
Système de traduction en temps réel des sermons de mosquée.
Un Raspberry Pi capte le micro de l'imam, envoie l'audio à un VPS cloud,
le VPS traduit par IA en 8 langues, et envoie la traduction (texte + voix)
sur les smartphones des fidèles qui scannent un QR code.
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

## Architecture globale (v3 — avril 2026)

```
MOSQUÉE                              VPS (CLOUD)                           FIDÈLES
────────                             ──────────                            ───────
Mixer imam                                                                
  → câble 3.5mm                                                           
  → USB sound card (~10 CHF)                                              
  → Raspberry Pi Zero W (~35 CHF)    ──WiFi/Ethernet──►  Backend FastAPI  
    (audio_capture.py)                                        │           
    streaming WebSocket                                       ├─ Deepgram Nova-3 (STT arabe)
    100ms chunks PCM                                          ├─ GPT-4.1 mini (traduction)
                                                              ├─ ElevenLabs Flash (TTS 5 langues)
                                                              ├─ Azure Neural TTS (TTS 3 langues)
                                                              │           
                                                              ▼           
QR code à l'entrée ──────────► Page web (index.html) ◄─── Smartphone fidèle
                                  WebSocket texte + audio    (scanne le QR)

Dashboard admin (admin.html) ◄── Téléphone du responsable
  • Bouton Start/Stop session        (protégé par PIN)
  • Bouton Coran (pause traduction)
  • Bouton Adhan (pause traduction)
```

## Stack technique (v3)

| Composant | Technologie | Rôle |
|-----------|------------|------|
| STT | Deepgram Nova-3 | Transcription arabe streaming (~300ms, 17 dialectes) |
| Traduction | GPT-4.1 mini | 8 langues en 1 appel, glossaire termes détectés (~400ms) |
| TTS 5 langues | ElevenLabs Flash v2.5 | fr, en, es, pt, tr (~75ms) |
| TTS 3 langues | Azure Neural TTS | ur, bs, sq (~200ms) |
| Backend | Python + FastAPI | Orchestre tout le pipeline |
| Frontend | HTML/JS (PWA) | Page fidèles + dashboard admin |
| Capture audio | Raspberry Pi Zero W | Streaming micro → VPS via WebSocket |
| Hébergement | VPS Linux | Un serveur, une mosquée (multi-mosquée prévu plus tard) |
| Glossaire | 408 termes JSON | Scan par phrase, injection termes détectés dans prompt GPT |

### Ancien stack (v2 — supprimé)
- ~~Azure Speech Translation SDK (STT + traduction)~~ → trop lent (3-5s), remplacé par Deepgram + GPT-4.1 mini
- ~~OpenAI Whisper (fallback STT)~~ → supprimé
- ~~Google Gemini Flash (fallback traduction)~~ → supprimé

## Pipeline détaillé d'une phrase

```
1. L'imam parle → le mixer capte le son
2. Raspberry Pi envoie l'audio au VPS (WebSocket, chunks 100ms, PCM 16kHz 16-bit mono)
3. Backend pousse l'audio dans Deepgram Nova-3 (streaming)
4. Deepgram renvoie le texte partiel → broadcast "partial" aux fidèles connectés
5. Deepgram renvoie la phrase complète en arabe
6. Backend scanne le glossaire → trouve les termes religieux dans la phrase
7. Backend envoie à GPT-4.1 mini (1 seul appel) :
   - La phrase arabe + les termes détectés du glossaire
   - Demande traduction uniquement dans les langues où des fidèles sont connectés
8. GPT répond en JSON avec les traductions
9. Backend broadcast "final_text" aux fidèles (texte affiché immédiatement)
10. Backend lance TTS en parallèle (seulement les langues connectées) :
    - ElevenLabs Flash v2.5 pour fr, en, es, pt, tr
    - Azure Neural TTS pour ur, bs, sq
11. Backend broadcast "audio_chunk" aux fidèles (audio joué)

Latence totale estimée : ~800ms à 1.2 seconde
```

## Clés API nécessaires (dans le fichier .env)
- DEEPGRAM_API_KEY : pour Deepgram Nova-3 (STT arabe streaming)
- OPENAI_API_KEY : pour GPT-4.1 mini (traduction 8 langues)
- ELEVENLABS_API_KEY : pour ElevenLabs Flash v2.5 (TTS 5 langues)
- AZURE_SPEECH_KEY : pour Azure Neural TTS uniquement (TTS 3 langues : ur, bs, sq)
- AZURE_SPEECH_REGION : westeurope
- ADMIN_PIN : code secret dashboard admin
- MOSQUE_NAME : nom de la mosquée

## Structure des fichiers (v3 — découpée)

```
backend/
  main.py              ← Point d'entrée FastAPI, routes, WebSocket, état global
  deepgram_stt.py      ← Connexion Deepgram Nova-3, streaming STT arabe
  gpt_translator.py    ← Traduction GPT-4.1 mini + scan glossaire 408 termes
  tts_engine.py        ← Routeur TTS (ElevenLabs Flash + Azure Neural)
  glossary.json        ← 408 termes religieux islamiques (8 langues)
  requirements.txt
  Dockerfile

frontend/
  index.html           ← Page smartphone fidèles (inchangée)
  admin.html           ← Dashboard admin (+ boutons Coran/Adhan)
  manifest.json

scripts/
  audio_capture.py     ← Capture micro Raspberry Pi → WebSocket VPS

docker-compose.yml
.env
```

## Les 8 langues cibles

| # | Langue | Code | TTS Provider |
|---|--------|------|--------------|
| 1 | Français | fr | ElevenLabs Flash v2.5 |
| 2 | English | en | ElevenLabs Flash v2.5 |
| 3 | Español | es | ElevenLabs Flash v2.5 |
| 4 | Português | pt | ElevenLabs Flash v2.5 |
| 5 | Türkçe | tr | ElevenLabs Flash v2.5 |
| 6 | Urdu | ur | Azure Neural TTS (ur-PK) |
| 7 | Bosanski | bs | Azure Neural TTS (bs-BA) |
| 8 | Shqip (Albanais) | sq | Azure Neural TTS (sq-AL) |

## Stratégie TTS hybride
- 5 langues via ElevenLabs Flash v2.5 (model_id: `eleven_flash_v2_5`) → ~75ms, voix naturelle
- 3 langues via Azure Neural TTS → ~200ms
- Choix ElevenLabs confirmé par Boualem : qualité voix naturelle prioritaire (fidèles écoutent 30 min)

## Glossaire islamique — stratégie d'injection
- Fichier : backend/glossary.json (408 termes, 8 langues)
- On n'envoie PAS les 408 termes à chaque appel GPT (trop cher et lent)
- On scanne la phrase arabe → on détecte les termes du glossaire présents → on envoie seulement ceux-là (2-5 termes par phrase en général)

## Boutons Coran / Adhan (nouveau v3)
- Dashboard admin : 2 boutons "Coran" et "Adhan"
- Quand activé → backend met la traduction en pause (arrête d'envoyer à Deepgram)
- Les fidèles voient un message : "Récitation du Coran en cours" ou "Appel à la prière"
- Le responsable appuie à nouveau → reprise de la traduction
- Pas de détection automatique — bouton manuel uniquement

## QR Code — accès fidèles
- URL unique : `https://khutbabox.com` (une mosquée pour le MVP)
- QR code imprimé et affiché à l'entrée de la mosquée
- Le fidèle scanne → choisit sa langue et voix → écoute en direct
- Multi-mosquée prévu plus tard (URLs par mosquée : /mosquee-geneve, etc.)

## Matériel par mosquée
- Raspberry Pi Zero W : ~35 CHF
- USB sound card : ~10 CHF
- Câble 3.5mm : ~5 CHF
- Total matériel : ~50 CHF (une seule fois)
- Connexion : WiFi ou Ethernet (les deux supportés)

## Protocole WebSocket smartphones — 4 types de messages
```json
{ "type": "partial", "lang": "fr", "text": "texte partiel..." }
{ "type": "final_text", "lang": "fr", "text": "phrase complète" }
{ "type": "audio_chunk", "lang": "fr", "data": "base64...", "format": "mp3" }
{ "type": "status", "mode": "quran" }
```

## Coûts estimés (v3)
- Deepgram Nova-3 : ~$0.23/sermon 30min
- GPT-4.1 mini : ~$0.46/sermon 30min
- ElevenLabs Flash v2.5 (5 langues) : ~$4.50/sermon 30min
- Azure Neural TTS (3 langues) : ~$0.86/sermon 30min
- **Total par sermon : ~$6**
- **Total mensuel (4 khutbas vendredi) : ~$24 + $10 VPS = ~$34/mois**
- Pricing SaaS : à définir après tests

## Dépendances Python (requirements.txt)
```
fastapi
uvicorn
deepgram-sdk>=3.0.0
openai>=1.0.0
httpx
python-dotenv
azure-cognitiveservices-speech>=1.40.0
```

## Décisions d'architecture clés (avril 2026)
1. **Audio passe toujours par le backend** — les clés API ne quittent jamais le VPS (sécurité)
2. **Un seul appel GPT pour toutes les langues** — moins cher, presque aussi rapide que 8 appels séparés
3. **Glossaire scanné par phrase** — on injecte seulement les termes détectés (pas les 408 à chaque fois)
4. **TTS seulement pour les langues connectées** — pas de TTS gaspillé si personne n'écoute en turc
5. **Frontend inchangé** — même interface, même protocole WebSocket
6. **Une mosquée par VPS pour le MVP** — multi-mosquée plus tard
7. **Dashboard admin web + PIN** — pas d'app mobile dédiée

## Roadmap v3

### Déjà fait ✅
- Frontend index.html (page fidèles PWA)
- Frontend admin.html (dashboard admin)
- scripts/audio_capture.py (capture micro streaming WebSocket)
- backend/glossary.json (408 termes religieux, 8 langues)
- Docker Compose configuré
- Design v3 validé (brainstorming 2026-04-11)

### À faire — Migration v3
- Réécrire backend en fichiers découpés (main.py, deepgram_stt.py, gpt_translator.py, tts_engine.py)
- Intégrer Deepgram Nova-3 (STT arabe streaming)
- Intégrer GPT-4.1 mini (traduction + scan glossaire)
- Garder TTS hybride (ElevenLabs + Azure Neural) avec parallélisation
- Ajouter boutons Coran/Adhan dans admin.html
- Ajouter message "status" WebSocket pour Coran/Adhan côté fidèles
- Générer QR code pour l'URL de la mosquée
- Tester bout en bout (micro → traduction → audio sur téléphone)
- Déployer sur VPS

### Phases futures (hors MVP)
- Multi-mosquée (URLs par mosquée, système de salles)
- Détection automatique Coran / Adhan (remplace bouton manuel)
- Dashboard admin avancé (stats latence, historique, nombre fidèles)
- Formules pricing (Vendredi seul / Quotidien / Pro)
