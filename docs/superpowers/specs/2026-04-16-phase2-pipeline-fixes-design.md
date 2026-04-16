# KhutbaBox — Phase 2 : Corrections pipeline temps réel

**Date** : 2026-04-16
**Branche** : feature/traducteur-v2

## Ce qu'on a fait en Phase 1 (même jour)

| Amélioration | Commit | Résultat |
|-------------|--------|----------|
| Buffer audio réduit 31→10 frames | `802e870` | -2s au démarrage |
| GPT warmup au démarrage | `802e870` | -4s sur la 1ère phrase (1.6s au lieu de 5s) |
| GPT keepalive toutes les 30s | `802e870` | Plus de cold start après les silences |

**Test Phase 1 (10 minutes)** : GPT stable à ~1s par segment, TTS ~280ms. MAIS on a découvert 4 problèmes :

## Problèmes découverts pendant le test de 10 minutes

### Problème 1 : Retard qui s'accumule (~1 min après 10 min)
`on_final_transcript` dans `main.py` **bloquait** la boucle de réception Deepgram pendant tout le pipeline (GPT ~1s + TTS ~0.3s = ~1.3s). Pendant ce temps, Deepgram envoyait des segments qui s'empilaient dans une file d'attente. Dès qu'un pic GPT arrivait (4s, on en a eu 2-3), la file ne rattrapait jamais son retard. Effet boule de neige.

### Problème 2 : Segments trop courts (3-5 mots)
Deepgram envoie des `is_final` toutes les 1-3 secondes avec seulement 3-5 mots arabes. GPT traduisait chaque petit bout séparément sans contexte → traduction décousue et bizarre.

### Problème 3 : Chiffres en anglais
GPT écrivait "8" en digit. ElevenLabs lisait "eight" au lieu de "huit". Le prompt GPT ne demandait pas d'écrire les chiffres en toutes lettres.

### Problème 4 : Reprise bizarre après un silence de l'imam
Le code ne distinguait pas `is_final` (segment audio traité) de `speech_final` (l'imam a fait une vraie pause). Résultat : premier segment trop court après un silence + rolling context pollué → traduction bizarre.

## Solutions appliquées en Phase 2 (commit `d460dc8`)

### Fix 1 : Traduction non-bloquante (`main.py`)
**Avant** : `await traduire(...)` bloquait la réception Deepgram pendant ~1.3s.
**Après** : `asyncio.create_task(_process_translation(...))` lance la traduction en tâche de fond. La boucle Deepgram n'est JAMAIS bloquée. Plus de file d'attente qui s'accumule.

### Fix 2 : Accumulateur de segments (`main.py`)
**Avant** : chaque `is_final` de 3-5 mots → 1 appel GPT.
**Après** : les segments sont accumulés dans un buffer. La traduction se déclenche quand :
- Le buffer atteint **8 mots** (seuil `WORD_THRESHOLD`)
- OU l'imam fait une pause (`speech_final` de Deepgram)
- OU **4 secondes** se sont écoulées sans atteindre 8 mots (timer de sécurité)

Résultat : GPT reçoit des phrases de 8-15 mots au lieu de 3-5 → meilleure traduction, moins d'appels, moins cher.

### Fix 3 : Détection `speech_final` (`deepgram_stt.py`)
**Avant** : le code ne vérifiait que `is_final`.
**Après** : le code vérifie aussi `speech_final` (champ Deepgram qui indique une pause de l'imam de 1s+). Quand `speech_final` est détecté, le buffer est flush immédiatement, même s'il a moins de 8 mots.

### Fix 4 : Chiffres en toutes lettres (`gpt_translator.py`)
Ajout de la règle 9 dans le prompt système :
> "Écris TOUS les nombres en toutes lettres (« huit milliards » pas « 8 milliards »)."

### Fix 5 : Client HTTP réutilisé (`tts_engine.py`)
**Avant** : `httpx.AsyncClient()` créé à chaque appel ElevenLabs → nouvelle connexion TLS à chaque phrase.
**Après** : un seul `_http_client` créé au démarrage, réutilisé pour tous les appels. Gain : ~100-200ms par appel TTS.

## Fichiers modifiés

| Fichier | Ce qui a changé |
|---------|----------------|
| `backend/deepgram_stt.py` | Ajout callback `on_speech_final` + détection `speech_final` dans `_receive_loop` |
| `backend/main.py` | Accumulateur (`on_segment`, `_flush_accumulator`, `_accumulator_timeout`) + traduction non-bloquante (`_process_translation` via `asyncio.create_task`) |
| `backend/gpt_translator.py` | Règle 9 prompt (chiffres en toutes lettres) + `warmup()` + `_last_gpt_call` (Phase 1) |
| `backend/tts_engine.py` | Client `_http_client` persistant au lieu de nouveau client par appel |

## Flux du pipeline après les corrections

```
Imam parle
  → Raspberry Pi envoie audio (chunks 100ms)
  → Backend reçoit, pousse dans Deepgram

Deepgram envoie is_final (3-5 mots) :
  → on_segment() ajoute au buffer (instantané, ne bloque rien)
  → Si buffer ≥ 8 mots → _flush_accumulator()
  → Sinon → attend le prochain segment (timer 4s max)

Deepgram envoie speech_final (imam fait une pause) :
  → on_speech_final_handler() flush le buffer immédiatement

_flush_accumulator() :
  → Prend le texte accumulé (8-15 mots)
  → Lance asyncio.create_task(_process_translation(...))
  → Retourne IMMÉDIATEMENT (non-bloquant)

_process_translation() (en tâche de fond) :
  → Scanne glossaire → trouve les termes islamiques
  → GPT-4.1 mini traduit (streaming, ~1s)
  → Pour chaque langue finie : texte final + TTS → broadcast aux fidèles
  → Met à jour le rolling context (3 dernières phrases)
```

## Latence attendue après Phase 2

| Moment | Phase 1 | Phase 2 |
|--------|---------|---------|
| Tout premier démarrage | ~2s | ~2s (inchangé) |
| Parole continue | ~1.5s mais retard qui s'accumule | ~3-4s stable (pas de retard) |
| Après 10 minutes | ~1 min de retard | ~3-4s constant |
| Après un silence | ~2-3s | ~2-3s + flush immédiat |

## Options alternatives évaluées et rejetées

| Option | Pourquoi rejetée |
|--------|-----------------|
| Palabra.ai (STT+traduction) | 3-4x plus cher ($15-22/sermon vs $6), langues rares (ur/bs/sq) non confirmées |
| Azure Speech Translation | Latence 3-5s (c'est pour ça qu'on l'avait quitté), pas de glossaire injectable |
| Meta SeamlessStreaming | Licence non-commerciale, GPU coûteux, bs/sq absents |
| Wordly.ai | API fermée, pas conçu pour intégration custom |
| Soniox | Seul candidat intéressant — à surveiller pour le futur, prix non confirmé |

**Conclusion** : le pipeline Deepgram + GPT + ElevenLabs reste le meilleur choix. Les problèmes étaient des bugs de code, pas des limites d'architecture.
