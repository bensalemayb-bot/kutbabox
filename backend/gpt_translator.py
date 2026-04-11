"""
KhutbaBox — Traduction GPT-4.1 mini
Traduit l'arabe vers les langues demandées en un seul appel.
Scanne le glossaire islamique et injecte seulement les termes détectés.
"""

import os
import json
import time
import logging

from openai import AsyncOpenAI

logger = logging.getLogger("khutbabox.translator")

# ── Configuration ──

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GPT_MODEL = "gpt-4.1-mini"

# Noms complets des langues (pour le prompt GPT)
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

# ── Client OpenAI ──

_client: AsyncOpenAI | None = None


def init_translator():
    """Initialise le client OpenAI. Appeler une fois au démarrage."""
    global _client
    _client = AsyncOpenAI(api_key=OPENAI_API_KEY)


# ── Glossaire ──

_glossaire: dict = {}
_termes_arabes: list[str] = []


def charger_glossaire(chemin: str):
    """
    Charge le glossaire JSON et extrait les termes arabes pour la recherche.
    Appeler une fois au démarrage.
    """
    global _glossaire, _termes_arabes

    with open(chemin, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Parcourir toutes les catégories du glossaire
    for categorie, termes in data.items():
        if categorie.startswith("_"):
            continue  # Skip metadata
        if not isinstance(termes, dict):
            continue
        for terme_arabe, traductions in termes.items():
            if terme_arabe.startswith("_"):
                continue  # Skip _description
            if isinstance(traductions, dict):
                _glossaire[terme_arabe] = traductions

    # Trier par longueur décroissante pour matcher les expressions longues d'abord
    _termes_arabes = sorted(_glossaire.keys(), key=len, reverse=True)
    logger.info(f"Glossaire chargé : {len(_glossaire)} termes")


def scanner_glossaire(texte_arabe: str) -> dict[str, dict]:
    """
    Scanne le texte arabe et retourne les termes du glossaire détectés.
    Retourne: {terme_arabe: {transliteration, fr, en, ...}}
    """
    termes_trouves = {}
    for terme in _termes_arabes:
        if terme in texte_arabe:
            termes_trouves[terme] = _glossaire[terme]
    return termes_trouves


async def traduire(texte_arabe: str, langues: list[str]) -> dict[str, str]:
    """
    Traduit le texte arabe vers les langues demandées via GPT-4.1 mini.
    Injecte les termes du glossaire détectés dans la phrase.

    Retourne: {"fr": "traduction...", "en": "translation...", ...}
    """
    if not langues:
        return {}

    debut = time.time()

    # Scanner le glossaire pour les termes présents dans la phrase
    termes_detectes = scanner_glossaire(texte_arabe)

    # Construire la section glossaire du prompt
    glossaire_prompt = ""
    if termes_detectes:
        lignes = []
        for arabe, trads in termes_detectes.items():
            transliteration = trads.get("transliteration", "")
            exemples = ", ".join(
                f'{code}: "{trads[code]}"'
                for code in langues
                if code in trads
            )
            lignes.append(f'  - {arabe} ({transliteration}) → {exemples}')
        glossaire_prompt = (
            "\n\nTERMES ISLAMIQUES OBLIGATOIRES — utilise EXACTEMENT ces traductions :\n"
            + "\n".join(lignes)
        )

    # Construire les noms de langues demandées
    noms_langues = ", ".join(
        f"{code} ({LANGUES.get(code, code)})"
        for code in langues
    )

    system_prompt = (
        "Tu es un traducteur professionnel spécialisé dans les sermons islamiques (khoutba). "
        "Traduis le texte arabe dans les langues demandées. "
        "Utilise un registre solennel et respectueux. "
        "Aucune traduction créative ni interprétation libre. "
        "Réponds UNIQUEMENT en JSON avec les codes de langue comme clés."
        f"{glossaire_prompt}"
    )

    user_prompt = (
        f"Langues : {noms_langues}\n\n"
        f"Texte arabe : «{texte_arabe}»"
    )

    try:
        response = await _client.chat.completions.create(
            model=GPT_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=2000,
        )

        result = json.loads(response.choices[0].message.content)
        latence = int((time.time() - debut) * 1000)

        # Filtrer pour ne garder que les langues demandées
        traductions = {code: result[code] for code in langues if code in result}

        nb_termes = len(termes_detectes)
        logger.info(
            f"[GPT] {len(traductions)} langues, {nb_termes} termes glossaire, {latence}ms"
        )
        return traductions

    except Exception as e:
        logger.error(f"[GPT] Erreur traduction : {e}")
        return {}
