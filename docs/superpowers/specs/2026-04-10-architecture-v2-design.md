# KhutbaBox — Architecture v2 "Best of Breed"

> Date : 2026-04-10
> Auteur : Boualem (IA Factory, Genève) + Claude Code
> Statut : Validé

## Résumé

Refonte du pipeline de traduction KhutbaBox pour obtenir la meilleure
précision possible sur les sermons islamiques, avec une latence < 2.5 secondes.

Stratégie : chaque IA fait ce qu'elle fait de mieux (best of breed).

## Décision clé

| Composant | Ancien (v1) | Nouveau (v2) | Pourquoi |
|-----------|-------------|--------------|----------|
| STT | Azure Speech Translation | **Speechmatics** | 25% moins d'erreurs en arabe, tous dialectes, détection auto de langue |
| Traduction | Azure Translator (machine) | **GPT-4.1 mini** (LLM) | Comprend le sens religieux, respecte le glossaire islamique |
| TTS (5 langues) | ElevenLabs Flash v2.5 | **ElevenLabs Flash v2.5** (inchangé) | Déjà le meilleur |
| TTS (3 langues) | Azure Neural TTS | **Azure Neural TTS** (inchangé) | Seul à supporter ur/bs/sq |
| Fallback STT | OpenAI Whisper | **Supprimé** | Speechmatics est plus fiable |
| Fallback traduction | Google Gemini Flash | **Supprimé** | GPT-4.1 mini est plus fiable |

---

## 1. Pipeline complet

```
┌─────────────┐     audio PCM 16kHz      ┌──────────────────┐
│  Micro imam  │ ──── WebSocket ────────► │  Speechmatics    │
│  (capture)   │     ~100ms par frame     │  STT arabe       │
└─────────────┘                           │  (détection auto │
                                          │   de langue)     │
                                          └────────┬─────────┘
                                                   │ texte source
                                                   ▼
                                          ┌──────────────────┐
                                          │  GPT-4.1 mini    │
                                          │  Traduction ×8   │
                                          │  + glossaire     │
                                          │  islamique       │
                                          │  (380 termes)    │
                                          └────────┬─────────┘
                                                   │ texte traduit (8 langues)
                                          ┌────────┴─────────┐
                                          ▼                  ▼
                                  ┌──────────────┐  ┌──────────────┐
                                  │ ElevenLabs   │  │ Azure Neural │
                                  │ Flash v2.5   │  │ TTS          │
                                  │ (fr,en,es,   │  │ (ur, bs, sq) │
                                  │  pt, tr)     │  │              │
                                  └──────┬───────┘  └──────┬───────┘
                                         │                 │
                                         ▼                 ▼
                                  ┌────────────────────────────┐
                                  │  WebSocket → Smartphones   │
                                  │  texte + audio             │
                                  └────────────────────────────┘
```

## 2. Latence estimée

| Étape | Durée | Détail |
|-------|-------|--------|
| Capture micro | ~100ms | Frames PCM 16kHz via WebSocket |
| Speechmatics STT | ~500ms-1s | Reconnaissance phrase par phrase |
| GPT-4.1 mini traduction | ~1-1.5s | 8 langues en parallèle, streaming |
| TTS (ElevenLabs/Azure) | ~100-200ms | Génération vocale |
| Envoi WebSocket | ~50ms | Vers smartphones |
| **Total** | **~2-2.5s** | Comme un interprète humain |

## 3. Les 8 langues cibles

| # | Langue | Code | TTS Provider | Voix |
|---|--------|------|--------------|------|
| 1 | Français | fr | ElevenLabs Flash v2.5 | Adam (multilingue) |
| 2 | English | en | ElevenLabs Flash v2.5 | Adam (multilingue) |
| 3 | Español | es | ElevenLabs Flash v2.5 | Adam (multilingue) |
| 4 | Português | pt | ElevenLabs Flash v2.5 | Adam (multilingue) |
| 5 | Türkçe | tr | ElevenLabs Flash v2.5 | Adam (multilingue) |
| 6 | Urdu | ur | Azure Neural TTS | ur-PK-AsadNeural |
| 7 | Bosanski | bs | Azure Neural TTS | bs-BA-GoranNeural |
| 8 | Shqip | sq | Azure Neural TTS | sq-AL-IlirNeural |

## 4. Détection automatique de langue

Speechmatics détecte automatiquement la langue parlée par l'imam :

- Imam parle arabe → traduit vers 7 autres langues (pas besoin de traduire en arabe)
- Imam parle français → traduit vers 7 autres langues (pas besoin de traduire en français)
- Imam mélange arabe/français → Speechmatics gère le switch automatiquement
- Pack bilingue arabe-anglais (ar_en) disponible chez Speechmatics

Le backend adapte dynamiquement les langues cibles selon la langue détectée.

## 5. Glossaire islamique

### Rôle
Forcer GPT-4.1 mini à garder certains termes en arabe/translittération
au lieu de les traduire librement.

### Source
Fichier `backend/glossary.json` — 380 termes, 17 catégories, 8 langues.

### Intégration
Le glossaire est injecté dans le prompt de traduction. Seuls les termes
"à ne pas traduire" sont envoyés (pas les 380 complets — un sous-ensemble
de ~50-100 termes critiques pour garder le prompt court).

### Catégories principales
- Noms et Attributs d'Allah (89 termes)
- Concepts de sermon (40 termes)
- Vie quotidienne et société (33 termes)
- Morale et spiritualité (31 termes)
- Prières et adoration (24 termes)
- Formules rituelles (21 termes)
- + 11 autres catégories

## 6. Prompt de traduction (GPT-4.1 mini)

```
Tu es un traducteur professionnel spécialisé dans les sermons
islamiques (khoutba du vendredi).

GLOSSAIRE OBLIGATOIRE — ces termes ne sont JAMAIS traduits librement :
- الله → Allah
- الصلاة → la Salat
- صلى الله عليه وسلم → Salla Allahou Alayhi wa Sallam
- الحمد لله → Al-Hamdoulillah
... (termes critiques du glossaire)

RÈGLES :
1. Respecte le ton religieux et solennel
2. Garde les termes du glossaire tels quels
3. Traduis le SENS, pas mot à mot
4. Adapte les expressions idiomatiques arabes à la langue cible

Langue source détectée : [arabe/français/autre]
Traduis en : [langue cible]

Phrase : "[texte reconnu par Speechmatics]"
```

## 7. Pause Adhan / Prière

### MVP : Bouton manuel
- Le responsable mosquée appuie sur "Pause" dans le dashboard admin
- Tous les smartphones affichent : "Récitation en cours — traduction en pause"
- Le responsable appuie sur "Reprendre" quand la khoutba reprend

### Futur (v3) : Détection automatique
- IA détecte le Adhan et les récitations coraniques
- Suspension automatique de la traduction
- Bouton manuel gardé en secours

### Endpoint API
- `POST /api/session/pause` — met en pause
- `POST /api/session/resume` — reprend

### Message WebSocket
```json
{ "type": "status", "status": "paused", "message": "Récitation en cours" }
{ "type": "status", "status": "resumed", "message": "" }
```

## 8. Parcours utilisateur (fidèle)

1. Entre dans la mosquée
2. Voit un QR code affiché (mur, papier, écran)
3. Scanne avec son téléphone
4. Le navigateur ouvre `https://khutbabox.com/[nom-mosquee]`
5. Choisit sa langue (fr, en, es, pt, tr, ur, bs, sq)
6. Met ses écouteurs
7. L'imam parle → traduction texte + audio arrive automatiquement
8. Pendant le Adhan/prière → "Récitation en cours" s'affiche

Pas d'app à installer. Pas de compte à créer.

## 9. Dashboard admin

Accessible via `https://khutbabox.com/[nom-mosquee]/admin` + code PIN.

Affiche :
- Statut : "Imam parle" / "Silence" / "En pause"
- Nombre de fidèles connectés en temps réel
- Répartition par langue (ex: 12 fr, 5 tr, 3 en)
- Bouton Pause / Reprendre (Adhan, prière)
- Latence moyenne
- Historique des phrases traduites

## 10. Infrastructure

### Dans la mosquée
| Élément | Rôle | Prix |
|---------|------|------|
| Mini PC ou Raspberry Pi | Boîtier KhutbaBox (capture audio) | ~80-150 CHF |
| Micro USB ou câble depuis récepteur Bluetooth | Capter la voix | ~20-50 CHF |
| Connexion internet | Envoyer l'audio au VPS | Existant |
| QR code affiché | Accès fidèles | Gratuit |

### Cloud (VPS)
| Élément | Rôle |
|---------|------|
| VPS (Linux) | Héberge le backend FastAPI |
| Docker Compose | Orchestre les services |
| Nginx | Reverse proxy + HTTPS |
| Domaine khutbabox.com | URL publique |

### Connectivité
- Le boîtier envoie l'audio au VPS via internet (WiFi/4G de la mosquée)
- Les fidèles se connectent au VPS via leur propre connexion (4G/5G/WiFi)
- Pas besoin de WiFi dédié dans la mosquée

## 11. Clés API et coûts

| Service | Clé .env | Coût/mois |
|---------|----------|-----------|
| Speechmatics | `SPEECHMATICS_API_KEY` | ~15-20$ |
| OpenAI GPT-4.1 mini | `OPENAI_API_KEY` | ~10-15$ |
| ElevenLabs | `ELEVENLABS_API_KEY` | ~15$ |
| Azure Neural TTS | `AZURE_SPEECH_KEY` | ~5$ |
| VPS | — | ~10$ |
| **Total** | | **~55-65$/mois** |

Prix de vente SaaS : 349 CHF/mois → Marge : ~85%

## 12. Fichiers à modifier

| Fichier | Action | Détail |
|---------|--------|--------|
| `backend/main.py` | Modifier | Remplacer Azure Speech Translation par Speechmatics STT |
| `backend/main.py` | Modifier | Remplacer Azure Translator par GPT-4.1 mini |
| `backend/main.py` | Modifier | Ajouter détection auto de langue |
| `backend/main.py` | Modifier | Ajouter endpoints pause/resume |
| `backend/main.py` | Modifier | Charger glossary.json au démarrage |
| `backend/glossary.json` | Remplacer | Copier le glossary_v2 (380 termes) |
| `backend/requirements.txt` | Modifier | Ajouter `speechmatics`, retirer `google-generativeai` |
| `frontend/index.html` | Modifier | Afficher "Récitation en cours" pendant pause |
| `frontend/admin.html` | Modifier | Ajouter bouton Pause/Reprendre |
| `.env` | Modifier | Ajouter `SPEECHMATICS_API_KEY`, retirer `GEMINI_API_KEY` |
| `scripts/audio_capture.py` | Inchangé | Fonctionne déjà en WebSocket streaming |

## 13. Ce qui est supprimé

- `google-generativeai` (Gemini Flash) — plus de fallback traduction
- Azure Speech Translation SDK pour la traduction — gardé uniquement pour TTS
- Détection Coran/Adhan automatique — reportée à v3

## 14. Évolutions futures (hors MVP)

| Version | Fonctionnalité |
|---------|----------------|
| v2.1 | Passer GPT-4.1 mini → Claude Opus ou GPT-4o pour traductions plus nuancées |
| v2.2 | Voix clonée imam sur ElevenLabs |
| v2.3 | Plus de langues (malais, indonésien, bengali...) |
| v3 | Détection automatique Adhan/Coran par IA |
| v3.1 | Dashboard admin avancé (stats latence, historique, analytics) |
