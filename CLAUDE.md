# QueFaire — contexte projet pour Claude

Agrégateur d'activités et d'événements locaux. Un pipeline Python collecte,
normalise et exporte les événements ; un site Astro statique les présente avec
recherche en langage naturel, carte Leaflet et filtre temps de trajet.
Secteur MVP : **Isère (38)**. Tout est en **français** : code commenté, logs,
messages de commit, UI.

Docs détaillées : `docs/ARCHITECTURE.md` (fonctionnement) et `docs/ROADMAP.md`
(fait / à faire). Vue d'ensemble : `README.md`.

## Commandes

```bash
# Pipeline (Python ≥ 3.11)
pip install -r pipeline/requirements.txt
cd pipeline
python -m pytest tests -q                        # les tests (sans réseau ni LLM)
python -m quefaire crawl --sector isere --demo   # jeu de démo — voir AVERTISSEMENT
python -m quefaire discover-oa --sector isere    # découverte d'agendas OpenAgenda
python -m quefaire discover --sector isere       # découverte de sources par agent LLM

# Site (Node ≥ 20)
cd site && npm install
npm run dev      # http://localhost:4321
npm run build
```

## AVERTISSEMENT : données générées

`site/src/data/events.json` et `site/src/data/sector.json` sont **générés par
le workflow CI** (`refresh.yml`, crawl réel 2×/jour) et committés par le bot.
Un crawl local (surtout `--demo`) les écrase avec des données factices : **ne
jamais committer ces fichiers après un run local**. Si ça arrive, les
restaurer depuis le dernier commit avant de committer autre chose
(`git checkout <ref> -- site/src/data/`).

## Architecture en bref

```
pipeline/sources/isere.yaml       registre des sources (enabled: false par défaut,
                                  un humain valide avant activation)
pipeline/data/communes_isere.csv  géocodage hors-ligne commune → lat/lon
pipeline/quefaire/
  fetchers/    rss, ical, openagenda, html_llm (extraction LLM), social (RSS-Bridge+LLM)
  llm.py       résolution LLM principal/backup — voir ci-dessous
  normalize.py catégorie / public / gratuité par règles lisibles
  geocode.py   commune → coordonnées, zéro appel réseau
  dedupe.py    même événement via N sources → 1 fiche (on garde la plus riche)
  clarify.py   phrase LLM « en clair » pour les titres ambigus
  discovery.py agent LLM qui propose de nouvelles sources (YAML à relire)
  export.py    → site/src/data/{events,sector}.json

site/src/
  pages/index.astro    page principale : recherche, filtres, carte, grille —
                       toute la logique client est dans son <script> (vanilla JS)
  lib/nlsearch.js      parseur FR de requêtes libres + distance/temps de trajet
  components/EventCard.astro  carte événement (attributs data-* pour le filtrage)
  pages/evenement/[id].astro  pages détail générées au build

.github/workflows/refresh.yml   cron 2×/jour : crawl → commit data → build → Pages
```

## LLM : principal + backup

- `QUEFAIRE_LLM` (principal) et `QUEFAIRE_LLM2` (backup optionnel), format
  `provider:modèle` (ex. `gemini:gemini-3.5-flash`, `deepseek:deepseek-v4-flash`).
- `pipeline/quefaire/llm.py` fait un test de connexion minimal au premier appel
  du run et met la décision en cache (un seul test par process). Bascule sur le
  backup si le principal ne répond pas (quota épuisé, erreur).
- Lib : `autoagent-core` (providers supportés : OpenAI, Anthropic, DeepSeek,
  Gemini). Clés : `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`.
- Tout consommateur LLM (html_llm, social, clarify, discovery) passe par
  `llm.get_agent()` et **saute proprement** (log + skip) si aucun LLM n'est
  disponible — jamais de crash du crawl pour une intégration optionnelle.

## Conventions et philosophie

- **Dégradation gracieuse** : une source ou une intégration indisponible
  (clé absente, quota, réseau) est ignorée avec un log warning, le crawl
  continue. Sans aucune source activée, le pipeline bascule en démo pour ne
  jamais publier un site vide.
- **Validation humaine** : les outils de découverte produisent des entrées
  `enabled: false` ; un humain relit avant activation.
- **Site 100 % statique** : pas de serveur, pas de base. La recherche NL, la
  carte et les filtres tournent dans le navigateur. Le temps de trajet est une
  approximation à vol d'oiseau corrigé (`travelMinutes` dans `nlsearch.js`),
  bornée par le géocodage au centre de la commune.
- Tests : `pipeline/tests/test_pipeline.py`, sans réseau ni LLM réel (module
  `autoagent` factice injecté via `sys.modules`). Après toute modification du
  pipeline, les lancer ; le workflow CI les exécute aussi avant chaque crawl.
- Événements sans coordonnées (`lat`/`lon` null) : possibles quand la commune
  manque au CSV — le front doit les gérer (exclus du filtre temps et de la
  carte, visibles sinon).

## Git / PR

- Branche de travail imposée par la session, PR vers `main`. Ne jamais
  réutiliser une PR mergée : repartir de `origin/main` avec le même nom de
  branche pour tout travail de suite.
- Messages de commit en français, descriptifs.
