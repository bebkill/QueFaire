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
| Source | RSS, iCal, OpenAgenda, pages agenda | OpenStreetMap **+ DATAtourisme** |
| Rafraîchissement | 2×/jour | **hebdomadaire** — un musée ne « passe » pas |
| Repérage sur le site | pastille de date | badge « Permanent », icône dédiée, liseré coloré |

Les activités permanentes portent en plus leurs **horaires d'ouverture**, les
**distinctions officielles** qu'elles détiennent (Monument Historique, Musée de
France, Qualité Tourisme, Tourisme & Handicap…), un tag **✨ Insolite** pour les
curiosités hors des sentiers battus, et un lien direct vers le **site de
l'activité**. Elles se filtrent depuis la barre de recherche (« musée »,
« insolite », « valeurs sûres ») ou les chips dédiés.

Deux fournisseurs complémentaires : **OpenStreetMap** couvre le non-touristique
(cinéma de quartier, ludothèque, piscine) mais dépend de contributeurs bénévoles,
donc inégal en zone rurale ; **[DATAtourisme](https://www.datatourisme.fr/)** —
base nationale sous Licence Ouverte, alimentée par les offices de tourisme
eux-mêmes — apporte descriptions, horaires et labels. Les fiches d'un même lieu
sont fusionnées automatiquement.

> Les **notes d'avis** Google/TripAdvisor ne sont pas affichées : leurs
> conditions d'utilisation l'interdisent dans notre configuration (carte
> Leaflet). Détail et alternatives dans
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

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

### DATAtourisme (recommandé, gratuit)

Deux modes d'accès, au choix — sans l'un ni l'autre, la découverte fonctionne
avec OpenStreetMap seul :

| Mode | Secret | Coût en requêtes | Quand l'utiliser |
|---|---|---|---|
| **Flux** (« API locale ») | `DATATOURISME_FLUX_URL` | **1 par ville** | À privilégier : un flux créé dans le diffuseur est déjà filtré sur votre territoire |
| **API temps réel** | `DATATOURISME_API_KEY` | 1 par page de catalogue | Quand on ne dispose que d'une clé |

En mode API, le catalogue est parcouru en suivant `meta.next` (méthode
recommandée par DATAtourisme, la seule qui garantisse de ne rater aucun
résultat), avec `page_size=500` pour limiter le nombre de pages.

**Le filtre territorial se déclare dans le registre du secteur**, pas dans une
variable globale : le bon filtre dépend du territoire (l'Aveyron pour
Pont-de-Salars, l'Isère/Rhône/Ain pour Villemoirieu).

```yaml
sector:
  name: Pont-de-Salars
  radius_minutes: 60
  datatourisme_params: "department=12"   # filtre serveur du catalogue
```

Réglages complémentaires, par variable d'environnement :

| Variable | Rôle |
|---|---|
| `DATATOURISME_API_URL` | endpoint — `…/v1/placeOfInterest` ne rend que les lieux, moins de pages que `/catalog` qui inclut événements et produits |
| `DATATOURISME_API_PARAMS` | filtre de repli si le secteur n'en déclare pas |

Sans restriction, le catalogue national compte plus de 530 000 fiches ; la
pagination est plafonnée à 60 pages et **toute troncature est signalée par un
warning explicite** dans les logs, jamais silencieuse.

> ⚠️ Les noms exacts des paramètres de filtrage sont à confirmer sur
> [la documentation de l'API](https://api.datatourisme.fr/v1/docs) — ils n'ont
> pas pu être vérifiés lors de l'implémentation. C'est précisément pour cela
> qu'ils sont configurables sans modification de code.

Licence Ouverte Etalab : réutilisation libre, y compris commerciale, **à
condition de citer la source et la date de mise à jour** — l'attribution est
affichée sur la page « à propos » de chaque ville.

**Budget de requêtes.** DATAtourisme annonce 20–30 requêtes concurrentes,
~10 req/s en régime prolongé et 1000 req/heure. En mode flux, le coût est d'une
requête par ville et par passage hebdomadaire : on pourrait rafraîchir ~1000
villes en une heure avant d'approcher le plafond. En mode API avec un catalogue
correctement filtré, quelques pages par ville — soit encore une centaine de
villes par heure. Les villes sont traitées en séquence, avec un intervalle
minimal entre requêtes, un rejeu respectant `Retry-After` en cas de 429, et un
coupe-circuit à 1000 requêtes par run.

> **Règle de conception** : rester en mode « lot ». Un enrichissement fiche par
> fiche consommerait ~500 requêtes pour une seule ville, soit la moitié du quota
> horaire. C'est le seul scénario qui ferait mal.

### Notes d'avis (désactivées)

`ratings.py` sait interroger Google Places (`GOOGLE_PLACES_KEY`) et TripAdvisor
(`TRIPADVISOR_API_KEY`), mais **l'affichage est volontairement désactivé** :
Google interdit d'afficher du contenu Places à proximité d'une carte non-Google,
et TripAdvisor impose ses propres graphiques et un statut de partenaire. Les
distinctions officielles jouent ce rôle sans aucune contrainte.

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
