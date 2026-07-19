# Que faire ? — agrégateur d'activités et d'événements locaux

Les sorties locales sont éparpillées entre sites communaux, offices de tourisme
et réseaux sociaux. **Que faire ?** les collecte automatiquement, les normalise
et les présente sur un site statique léger, avec une recherche en langage
naturel : _« une sortie en famille ce week-end »_, _« un concert gratuit près
de moi »_.

Le MVP couvre le secteur **Isère (38)**. L'architecture est multi-secteurs dès
le départ : ajouter une région = ajouter un fichier de sources.

## Architecture

```
pipeline/                 Python — collecte, normalisation, export
├── sources/isere.yaml    ← LE registre : les sources du secteur
├── data/communes_isere.csv  géocodage hors-ligne (commune → lat/lon)
└── quefaire/
    ├── fetchers/         rss, ical, openagenda, html (extraction LLM)
    ├── normalize.py      catégorie / public / gratuité (règles lisibles)
    ├── geocode.py        commune → coordonnées, sans appel réseau
    ├── dedupe.py         même événement relayé par N sources → 1 fiche
    ├── discovery.py      agent LLM qui propose de nouvelles sources
    └── export.py         → site/src/data/{events,sector}.json

site/                     Astro — site statique
├── src/lib/nlsearch.js   parseur de requêtes FR côté client (0 dépendance)
├── src/pages/index.astro recherche + filtres + grille d'événements
└── src/pages/evenement/[id].astro   pages détail générées au build

.github/workflows/refresh.yml   cron 2×/jour : crawl → commit → build → Pages
```

### Flux de données

1. **Collecte** — chaque source du registre est crawlée selon son type :
   - `rss` / `ical` : flux structurés des sites communaux (parseurs internes) ;
   - `openagenda` : l'API OpenAgenda (une clé gratuite couvre des dizaines de
     communes) — variable `OPENAGENDA_KEY` ;
   - `html` : pages agenda sans flux — un agent
     [autoagent-core](https://pypi.org/project/autoagent-core/) en extrait les
     événements en JSON (variable `QUEFAIRE_LLM`, ex. `gemini:gemini-3.5-flash`).
2. **Normalisation** — catégorie, public et gratuité sont déduits par des
   règles lisibles (`normalize.py`) ; les communes sont géocodées via un CSV
   local, sans appel réseau.
3. **Déduplication** — un événement relayé par la mairie ET l'office de
   tourisme ne sort qu'une fois (on garde la fiche la plus riche).
4. **Export** — JSON consommés par Astro au build. Le site est **entièrement
   statique** : rapide, sans base de données, hébergeable gratuitement.

### Recherche en langage naturel

`site/src/lib/nlsearch.js` transforme la requête libre en filtre structuré,
dans le navigateur (aucun serveur) : dates relatives (« ce week-end »,
« aujourd'hui », « demain », « cette semaine »…), catégories et synonymes,
public (« en famille »), gratuité, communes du secteur, « près de moi »
(géolocalisation + tri par distance), et recherche plein texte pour le reste.

## Démarrer

```bash
# Pipeline (Python ≥ 3.11)
pip install -r pipeline/requirements.txt
cd pipeline
python -m quefaire crawl --sector isere --demo   # jeu de données de démo
python -m pytest tests -q

# Site (Node ≥ 20)
cd ../site
npm install
npm run dev        # http://localhost:4321
```

Sans l'option `--demo`, le crawl utilise les sources **activées** du registre.
Tant qu'aucune source n'est activée, le pipeline bascule en démo tout seul
pour ne jamais publier un site vide.

## Automatisation (données fraîches)

`.github/workflows/refresh.yml` tourne 2×/jour :
crawl → tests → commit des JSON s'ils ont changé → build Astro → déploiement
GitHub Pages. À configurer dans le dépôt :

1. **Settings → Pages** : source « GitHub Actions » ;
2. **Secrets** (optionnels, activent les sources réelles) : `OPENAGENDA_KEY`,
   et pour l'extraction LLM une clé de provider (`GEMINI_API_KEY`,
   `DEEPSEEK_API_KEY` ou `ANTHROPIC_API_KEY`) + variable `QUEFAIRE_LLM` au
   format `provider:modèle` (ex. `gemini:gemini-3.5-flash`).

### Principal + backup LLM

`QUEFAIRE_LLM2` déclare un second provider de secours (ex.
`deepseek:deepseek-v4-flash`). Au premier appel de chaque run, un test de
connexion minimal départage : le principal (`QUEFAIRE_LLM`) est utilisé s'il
répond, sinon le pipeline bascule automatiquement sur le backup — utile
quand le quota gratuit d'un provider est épuisé (ex. Gemini, 20 req/jour en
palier gratuit). La bascule joue aussi **en cours de run** : si le quota du
principal meurt entre deux sources, l'appel en échec est rejoué sur le
backup et le principal est écarté pour le reste du run.
DeepSeek est un bon choix de backup : 5 M tokens offerts à l'inscription,
puis ~0,14 $/0,28 $ par million de tokens (input/output) — largement
suffisant pour ce volume. Voir `pipeline/quefaire/llm.py`.

## Référencer des sources

Le registre est un simple YAML (`pipeline/sources/isere.yaml`). Deux outils
de découverte automatique produisent des entrées prêtes à coller :

```bash
# Agendas OpenAgenda de toutes les communes du secteur (dédupliqués par UID,
# officiels en premier). --strict ne garde que les agendas citant la commune.
OPENAGENDA_KEY=... python -m quefaire discover-oa --sector isere
OPENAGENDA_KEY=... python -m quefaire discover-oa --communes "Bourgoin-Jallieu,Crémieu" --strict

# Agent LLM : visite les sites communaux, détecte flux RSS/iCal et pages agenda
QUEFAIRE_LLM=gemini:gemini-3.5-flash python -m quefaire discover --sector isere
```

Dans les deux cas, **un humain relit puis passe `enabled: true`** — la
qualité du registre reste maîtrisée.

## Réseaux sociaux (Facebook, Instagram)

Beaucoup de petites communes n'annoncent leurs événements que sur Facebook ou
Instagram. Meta n'offrant pas d'API publique de lecture (et le scraping direct
étant bloqué et contraire aux CGU), le pipeline passe par
[RSS-Bridge](https://github.com/RSS-Bridge/rss-bridge) — une instance
auto-hébergée qui transforme une page publique en flux RSS :

1. déployer une instance RSS-Bridge et renseigner `RSSBRIDGE_URL` ;
2. déclarer des sources `type: facebook` / `type: instagram` avec l'identifiant
   de la page en `url` (des exemples sont dans `isere.yaml`) ;
3. l'agent LLM (`QUEFAIRE_LLM`) transforme les posts récents en événements
   datés — un post n'étant pas un événement structuré, cette voie exige le LLM.

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — fonctionnement détaillé du
  pipeline et du site (collecte, LLM principal/backup, recherche NL, carte).
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — ce qui est fait, ce qui vient.
- [`CLAUDE.md`](CLAUDE.md) — contexte, commandes et conventions pour les
  sessions de développement assistées.
