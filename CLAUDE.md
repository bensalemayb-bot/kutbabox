# KhutbaBox — Guide pour Claude Code

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
- Capture audio : PyAudio (micro branché en USB)
- IA transcription : OpenAI Whisper (comprend l'arabe)
- IA traduction : Claude Haiku (traduit le texte)
- IA voix : ElevenLabs (lit la traduction à voix haute)
- Infrastructure : Docker Compose

## Clés API nécessaires (dans le fichier .env)
- OPENAI_API_KEY : pour Whisper
- ANTHROPIC_API_KEY : pour Claude traduction
- ELEVENLABS_API_KEY : pour la voix
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
- backend/requirements.txt rempli
- backend/main.py écrit et corrigé

### À faire dans cet ordre exact

ÉTAPE 1 — docker-compose.yml (prochaine étape)
Lance tout le projet en une seule commande.
Sans ce fichier Docker ne sait pas quoi faire.

ÉTAPE 2 — backend/Dockerfile
Recette pour construire le conteneur Python.
Dit à Docker comment installer Python
et les dépendances du requirements.txt

ÉTAPE 3 — frontend/index.html
Page PWA que les fidèles ouvrent sur leur téléphone.
Choix langue + voix + lecture audio temps réel.
Se connecte au WebSocket /ws/listen du backend.

ÉTAPE 4 — frontend/admin.html
Dashboard responsable mosquée.
Start/stop session, monitoring latence, historique.
Protégé par PIN via header X-Admin-Pin.

ÉTAPE 5 — .env
Remplacer les fausses clés par les vraies clés API
OpenAI, Anthropic, ElevenLabs.

ÉTAPE 6 — Premier test local
Lancer docker compose up --build
Tester sur téléphone via http://IP-ordinateur
Vérifier que la page s'ouvre.

ÉTAPE 7 — Test audio complet
Simuler un vrai sermon avec audio arabe.
Vérifier détection Coran, traduction, voix.

ÉTAPE 8 — Corrections
Corriger ce qui ne marche pas.

ÉTAPE 9 — Transfert Raspberry Pi
Copier le projet sur le Raspberry Pi.
Lancer le script setup.sh

### Règle absolue
Toujours suivre cet ordre sans sauter d'étape.
Valider que chaque étape fonctionne avant
de passer à la suivante.
