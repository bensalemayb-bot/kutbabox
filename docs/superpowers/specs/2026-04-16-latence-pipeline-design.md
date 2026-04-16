# Réduction de la latence du pipeline KhutbaBox

**Date** : 2026-04-16
**Branche** : feature/traducteur-v2
**Commit de référence** : fae052a (pipeline v2 fonctionnel)

## Problèmes constatés

### 1. Démarrage trop lent (~8 secondes)
Quand l'imam commence à parler, le fidèle attend ~8 secondes avant de voir la première traduction.

**Causes :**
- Le backend bufferise 31 frames audio (~3.1s) avant de connecter Deepgram (main.py ligne 264)
- Le premier appel GPT prend ~5s (cold start : connexion HTTPS/TLS à créer)

### 2. Lent + traduction bizarre après un silence de l'imam (~4-7 secondes)
Quand l'imam s'arrête de parler puis reprend, le système met du temps à redémarrer et les premières traductions sont de mauvaise qualité.

**Causes :**
- Après 30-60s de silence, la connexion TCP vers OpenAI se ferme
- Le prochain appel GPT doit recréer la connexion = même problème que le cold start (~3-5s)

### Ce qui fonctionne bien (ne pas toucher)
- La parole continue : latence ~1.5s (Deepgram 300ms + GPT 1000ms + ElevenLabs 280ms)
- Le TTS se lance déjà en parallèle par langue (chaque langue n'attend pas les autres)
- Le streaming GPT token par token fonctionne bien

## Solutions retenues

### Amélioration 1 : Réduire le buffer audio initial

**Fichier** : `backend/main.py`
**Changement** : Passer de `range(30)` à `range(9)` (ligne 264)
**Gain** : -2 secondes au démarrage
**Risque** : Aucun. 10 frames (~1s d'audio) suffisent pour que Deepgram commence à traiter.

### Amélioration 2 : Préchauffer GPT + garder la connexion chaude

**Fichiers** : `backend/gpt_translator.py` + `backend/main.py`

**Partie A — Préchauffage au démarrage :**
- Ajouter une fonction `warmup()` dans `gpt_translator.py`
- Envoyer un petit appel GPT à vide au démarrage du backend (dans `on_startup()`)
- La connexion HTTPS/TLS est créée une fois pour toutes
- **Gain** : -4 secondes sur la première traduction

**Partie B — Garder la connexion chaude pendant les silences :**
- Tracker le timestamp du dernier appel GPT
- Si aucun appel depuis ~30 secondes, envoyer un mini appel warmup
- La connexion TCP reste ouverte, pas de cold start quand l'imam reprend
- **Gain** : -2-3 secondes après chaque silence de l'imam
- **Coût** : Négligeable (~$0.001 par appel warmup)

## Latence attendue après les améliorations

| Moment | Avant | Après |
|--------|-------|-------|
| Tout premier démarrage | ~8s | ~2s |
| Parole continue | ~1.5s | ~1.5s (inchangé) |
| Après un silence de l'imam | ~4-7s | ~2-3s |

## Ce qu'on ne fait PAS (et pourquoi)

- **Pas d'accumulateur de mots** : la parole continue fonctionne déjà bien à ~1.5s, un accumulateur ajouterait de la latence
- **Pas de changement d'endpointing** : le réglage actuel (1000ms) fonctionne bien
- **Pas de changement de provider STT** : Deepgram Nova-3 est le bon choix (rapide, pas cher, 17 dialectes arabes)
- **Pas de TTS en parallèle avec le streaming GPT** : déjà fait dans le code actuel
