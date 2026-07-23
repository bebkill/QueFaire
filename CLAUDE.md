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
  Chaque variable accepte une **liste séparée par des virgules** pour empiler
  plusieurs backups (ex. `QUEFAIRE_LLM2="deepseek:…,groq:…,mistral:…"`).
- **Deux chaînes indépendantes** (classe `_Chain`) : *crawl* (`QUEFAIRE_LLM`
  /`QUEFAIRE_LLM2`, extraction html/social + discover) et *clarify*
  (`QUEFAIRE_LLM_CLARIFY`/`QUEFAIRE_LLM_CLARIFY2`). Donner un modèle dédié à
  clarify lui offre son propre quota (ex. crawl sur `deepseek`, clarify sur
  `mistral`) ; sans lui, clarify réutilise la chaîne du crawl et se saute si
  celle-ci a déjà basculé (budget tendu).
- `pipeline/quefaire/llm.py` fait un test de connexion minimal au premier appel
  d'une chaîne et met la décision en cache. Le quota peut aussi mourir **en
  cours de run** (palier gratuit Gemini : 20 req/jour) : les appels passent par
  `run_llm()`, qui gère trois cas — **quota** (429/rate limit) → déclasse le
  provider pour tout le run et bascule ; **erreur serveur transitoire** (5xx,
  surcharge, timeout — vécu : salves de 503 Gemini) → rejoue sur les backups
  **pour cet appel seulement**, sans déclasser ; **réponse vide** (Gemini sur
  les grosses pages) → idem, secours pour cet appel. `get_agent()` reste réservé
  à discovery (outils `@agent.tool`).
- **Répétabilité** : les agents sont créés avec `temperature=0` (`_make_agent`)
  pour que deux crawls rapprochés donnent le même résultat. La stabilité dépend
  surtout du provider **principal** : préférer un provider payant/stable en tête
  (ex. `QUEFAIRE_LLM=deepseek:…`) et reléguer le palier gratuit Gemini en dernier
  backup (ou le retirer), sinon ses coupures (429/503) changent le résultat d'un
  run à l'autre.
- Lib : `autoagent-core`. Providers natifs : OpenAI, Anthropic, DeepSeek,
  Gemini, **Groq**. Providers OpenAI-compatibles branchés via l'adaptateur
  openai + `base_url` (voir `_OPENAI_COMPATIBLE` dans `llm.py`) : **Mistral**,
  z.ai (`zai`), Kimi (`kimi`/`moonshot`). Clés : `GEMINI_API_KEY`,
  `DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`,
  `ZAI_API_KEY`, `MOONSHOT_API_KEY`.
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
