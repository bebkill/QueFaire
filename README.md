# QueFaire — agrégateur d'activités et d'événements locaux

Les sorties locales sont éparpillées entre sites communaux, offices de tourisme
et agendas associatifs. **QueFaire** les collecte automatiquement, les
normalise et les présente sur un site statique léger, avec une recherche en
langage naturel : _« une sortie en famille ce week-end »_, _« un concert
gratuit près de moi »_.

## Le modèle « épicentre »

On ne raisonne pas par département mais par **épicentre** : une commune de
référence + un rayon en **temps de trajet** (~1 h de voiture ≈ 48 km). Un
événement est retenu s'il tombe dans ce rayon, quel que soit son département —
le nord-Isère est plus proche de Lyon et de l'Ain que du sud-Isère.

Une ville = un fichier `pipeline/sources/<commune>.yaml`. Le site est
**multi-villes** : un portail (`/villes/`) laisse choisir son épicentre, puis
chaque ville a son agenda complet sous `/<ville>/`.

Épicentres actifs en v1 :

| Ville | Secteur | Rayon |
|---|---|---|
| **Villemoirieu** | nord-Isère → Lyon (69) + Ain (01) | 60 min |
| **Pont-de-Salars** | Aveyron (Lévézou, Rodez) | 60 min |

## Deux types de contenus

| | Événement | Activité permanente |
|---|---|---|
| Exemple | concert du 12 avril, brocante | musée, château, parc d'attraction, cinéma, ludothèque |
| Ce qui le définit | une **date** | des **horaires** |
| Source | RSS, iCal, OpenAgenda, pages agenda | OpenStreetMap (`discover-places`) |
| Rafraîchissement | 2×/jour | **hebdomadaire** — un musée ne « passe » pas |
| Repérage sur le site | pastille de date | badge « Permanent », icône dédiée, liseré coloré |

Les activités permanentes portent en plus, quand l'information existe, une
**note d'avis** (Google ou TripAdvisor), leurs **horaires d'ouverture**, un tag
**✨ Insolite** pour les curiosités hors des sentiers battus, et un lien direct
vers le **site de l'activité**. Elles se filtrent depuis la barre de recherche
(« musée », « insolite », « bien noté ») ou les chips dédiés.

## Démarrer

```bash
# Pipeline (Python ≥ 3.11)
pip install -r pipeline/requirements.txt
cd pipeline
python -m pytest tests -q                              # tests, sans réseau ni LLM
python -m quefaire crawl --sector villemoirieu --demo  # jeu de démo

# Site (Node ≥ 20)
cd ../site
npm install
npm run dev        # http://localhost:4321
```

> ⚠️ Les JSON sous `site/src/data/` et `pipeline/cache/` sont **produits par la
> CI**. Un crawl local (surtout `--demo`) les écrase : ne pas les committer.

Sans `--demo`, le crawl utilise les sources **activées** du registre. Tant
qu'aucune source n'est activée, le pipeline bascule en démo tout seul pour ne
jamais publier un site vide.

## Commandes du pipeline

```bash
python -m quefaire sectors --active            # villes ayant des sources activées
python -m quefaire crawl --sector <ville>      # collecte → export JSON
python -m quefaire discover-places --sector <ville>  # (RÉSEAU) activités PERMANENTES
                                               # via OpenStreetMap ; --limit N pour
                                               # un essai, --no-llm / --no-ratings
python -m quefaire discover-oa --sector <ville>   # agendas OpenAgenda du secteur
python -m quefaire discover --sector <ville>      # découverte par agent LLM
python -m quefaire evaluate-source <url>          # événements uniques d'une URL candidate
python -m quefaire suggest --sector <ville>       # candidates → issues (workflow)
python -m quefaire add-source --file issue.md     # applique un bloc YAML approuvé
python -m quefaire build-geo --sector <ville> --departments 38,69,01,73
                                                  # (RÉSEAU) table de communes du rayon
```

## Ajouter une ville

1. `pipeline/sources/<commune>.yaml` — bloc `sector:` (`center_lat`,
   `center_lon`, `radius_minutes`) puis la liste des sources ;
2. `python -m quefaire build-geo --sector <commune> --departments …` pour
   générer `pipeline/data/communes_<commune>.csv` (géocodage hors-ligne) ;
3. activer au moins une source (`enabled: true`).

Le prochain crawl CI publie la ville, le portail l'affiche, les routes
`/<commune>/…` sont générées automatiquement. Aucun autre fichier à toucher.

## Référencer des sources

Le registre est un simple YAML. Deux outils de découverte produisent des
entrées prêtes à relire :

```bash
# Agendas OpenAgenda de toutes les communes du secteur (dédupliqués par UID,
# officiels en premier). --strict ne garde que les agendas citant la commune.
OPENAGENDA_KEY=... python -m quefaire discover-oa --sector villemoirieu
OPENAGENDA_KEY=... python -m quefaire discover-oa --communes "Bourgoin-Jallieu,Crémieu" --strict

# Agent LLM : visite les sites communaux, détecte flux RSS/iCal et pages agenda
QUEFAIRE_LLM=gemini:gemini-3.5-flash python -m quefaire discover --sector villemoirieu
```

Dans les deux cas, les entrées sont générées `enabled: false` : **un humain
relit puis active**. Le site expose aussi un formulaire « proposer une source »
qui ouvre une issue pré-remplie, traitée par le même circuit de validation.

## Automatisation

| Workflow | Déclencheur | Rôle |
|---|---|---|
| `refresh.yml` | cron 2×/jour | crawl de chaque ville active → commit des JSON → build → GitHub Pages |
| `places.yml` | cron hebdo (+ manuel) | découverte des activités permanentes → commit → déclenche le redéploiement |
| `discover.yml` | cron hebdo | propose de nouvelles sources sous forme d'issues |
| `apply-source.yml` | issue labellisée `approved` | ajoute la source au registre |
| `close-suggestions.yml` | manuel | ferme en lot les suggestions en attente |

À configurer dans le dépôt :

1. **Settings → Pages** : source « GitHub Actions » ;
2. **Secrets** (optionnels, activent les sources réelles) : `OPENAGENDA_KEY` et
   une clé de provider LLM (`GEMINI_API_KEY`, `DEEPSEEK_API_KEY`,
   `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`…) ;
3. **Variables** : `QUEFAIRE_LLM` au format `provider:modèle`
   (ex. `deepseek:deepseek-v4-flash`), `QUEFAIRE_LLM2` pour les backups.

### Notes d'avis (optionnel)

Renseigner **une** des deux clés active les notes sur les activités permanentes :
`GOOGLE_PLACES_KEY` (Places API New — meilleure couverture des petits lieux
ruraux) ou `TRIPADVISOR_API_KEY` (Content API). Sans clé, les activités sont
publiées **sans note** et le site n'affiche simplement pas d'étoiles : c'est un
fonctionnement normal, pas une panne. Les notes sont mises en cache 90 jours,
donc la facture d'API reste négligeable.

### LLM : principal + backups

`QUEFAIRE_LLM` désigne le provider principal, `QUEFAIRE_LLM2` une liste de
backups séparés par des virgules. Un test de connexion départage au premier
appel, et la bascule joue aussi **en cours de run** : quota épuisé → le
provider est déclassé pour le reste du crawl ; erreur serveur ou réponse vide →
l'appel seul est rejoué sur un backup. Si plus aucun provider ne répond, les
étapes LLM sont sautées avec un warning — le crawl ne casse jamais.

Détails et liste des providers : [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — fonctionnement détaillé du
  pipeline et du site.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — ce qui est fait, ce qui vient.
