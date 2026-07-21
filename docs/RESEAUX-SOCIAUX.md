# Facebook / Instagram : état des lieux des voies d'accès (juillet 2026)

Contexte : QueFaire ne fait que **référencer** les événements annoncés sur les
pages publiques et **renvoie l'utilisateur vers le post d'origine** — le
trafic revient vers Meta. Intuition légitime : « on leur ramène du monde,
ça devrait aller ». Vérification faite, voici ce que ça vaut réellement.

## Ce que dit Meta (CGU et APIs)

- Les CGU interdisent la **collecte automatisée sans permission écrite**,
  quelle que soit l'intention ou la direction du trafic. Il n'existe pas
  d'exception « je renvoie vers la plateforme ».
- Nuance jurisprudentielle importante (US) : *Meta v. Bright Data* (jan.
  2024) — un juge fédéral a conclu que les CGU ne s'appliquent qu'aux
  utilisateurs **connectés** ; le scraping *logged-out* de données publiques
  ne les viole pas, et Meta a renoncé à faire appel. Ça ne crée aucun droit
  d'accès pour autant : Meta bloque techniquement (IP de datacenters
  surtout), et c'est ce blocage — pas le droit — qui rend RSS-Bridge fragile.
- Côté France/UE : les posts publics restent des données potentiellement
  personnelles (RGPD). Pour des annonces d'événements de mairies/assos
  destinées au public, l'intérêt légitime se défend bien, mais ça mérite
  une ligne dans la page « À propos ».

## Les voies officielles, une par une

| Voie | Ce que ça permet | Verdict pour QueFaire |
|---|---|---|
| **Page Public Content Access** (PPCA) | Lire le feed public de pages tierces | App Review + vérification business + contrats additionnels ; en pratique réservé aux gros acteurs (veille média). Inaccessible à notre échelle. |
| **API Événements** (`page/events`) | Lire les événements FB d'une page | Edge **restreint** : « cannot request access at this time ». Mort depuis 2018, même avec consentement. |
| **oEmbed Read** | HTML d'embed d'un post public dont on a l'URL | Review légère, mais usage **affichage uniquement** (extraire les données de l'embed est explicitement interdit) et il faut déjà connaître l'URL du post. Utile pour *afficher* le post source sur une fiche, pas pour collecter. |
| **Consentement du propriétaire de la page** ✅ | La commune/l'asso connecte sa page → token de page → lecture de **son** feed (`pages_read_engagement`) ; Instagram pro : Instagram Login → lecture de **ses** médias | **La voie réaliste.** Les posts récupérés passent ensuite dans l'extraction LLM existante, comme avec RSS-Bridge. Pilote possible sans App Review complète : donner un rôle (testeur) aux admins de pages volontaires dans l'app Meta. Passage à l'échelle : App Review + vérification business. |

## Et si un compte QueFaire s'abonnait aux pages ?

Idée naturelle, vérifiée : **ça ne fonctionne pas.**

- Le fil d'actualité d'un compte n'a **pas d'API** : Facebook a fermé
  `/me/home` (permission `read_stream`) vers 2014-2015 et n'a jamais rouvert ;
  l'API Instagram n'a jamais exposé la timeline des comptes suivis aux apps
  tierces. Suivre une page n'ouvre aucun accès programmatique.
- Exploiter ce compte imposerait du scraping **connecté** (cookies du compte),
  soit exactement le cas où les CGU s'appliquent sans ambiguïté (a contrario
  de *Bright Data*, qui a gagné parce que déconnecté) : détection
  d'automatisation, bannissement rapide du compte — et de l'app Meta liée.
  Créer un compte pour collecter est donc *pire* que le scraping anonyme.
- L'équivalent légitime existe dans l'autre sens : ce n'est pas QueFaire qui
  s'abonne à la page, c'est **la page qui connecte QueFaire** (voie
  consentement ci-dessus) — une action unique de l'admin, et l'API fournit
  ensuite les posts en continu (webhooks Pages : quasi temps réel).
- Le modèle « on suit et on lit » marche en revanche très bien là où il est
  prévu : Mastodon/Bluesky (API ouvertes), Telegram (Bot API), YouTube (RSS
  natif) — trivial à ingérer si des communes y publient un jour.

## La piste hashtag : officielle côté Instagram, inexistante côté Facebook

Objectif : que n'importe qui — mairie, asso, **particulier** — puisse faire
référencer un événement en ajoutant simplement un hashtag convenu
(ex. `#quefaire38`) à son post, sans gestion d'abonnements par page.

- **Instagram : oui, API officielle.** La [Hashtag Search API]
  (https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/hashtag-search/)
  (`ig_hashtag_search` → `recent_media`) retourne les posts **publics**
  portant un hashtag : caption, type de média, **permalink** (pour renvoyer
  vers le post — le modèle QueFaire). Prérequis : un compte Instagram
  professionnel QueFaire (un seul, pas d'abonnements à gérer), une app Meta,
  et l'App Review de la feature **Instagram Public Content Access** — plus
  accessible que la PPCA Facebook. Limites structurantes : 30 hashtags
  uniques / 7 jours (large : on en suit 1-3), ~`recent_media` ne couvre que
  les posts récents (≈ 24 h) — compatible avec le crawl 2×/jour —, le nom de
  l'auteur n'est pas fourni (on crédite via le lien du post), hashtags des
  Stories non couverts, comptes privés invisibles.
- **Facebook : non.** Aucune API de recherche par hashtag n'existe côté
  pages/posts publics. La piste hashtag est donc Instagram-only chez Meta.
- **Modération indispensable** : un hashtag public est ouvert au spam et au
  hors-sujet. Cohérent avec la philosophie du registre : les événements
  issus du hashtag passent par l'extraction LLM (qui écarte déjà les posts
  sans événement daté) puis par une relecture humaine avant publication,
  au moins au début.
- **Équivalents hors Meta, encore plus simples** (complémentaires, zéro
  App Review) : un **formulaire** sur le site (soumission → file de
  modération), une **adresse mail** (« envoyez l'affiche, on s'occupe du
  reste » — extraction LLM d'une photo/PDF), et les hashtags
  Mastodon/Bluesky (API ouvertes, triviales) si l'usage local émerge.

## Recommandation

1. **Court terme, sans Meta** : proposer aux communes/assos cibles de publier
   aussi sur **OpenAgenda** (gratuit, déjà ingéré nativement) ou d'exposer un
   flux RSS. Un mail à la mairie est souvent la solution la plus rapide — et
   ces sources-là sont stables.
2. **Pilote « consentement »** : créer l'app Meta QueFaire, embarquer 2-3
   pages volontaires (ex. Café-Crém, CAPI) via un rôle dans l'app, lire leur
   feed par l'API officielle, extraction LLM inchangée. C'est aligné avec
   l'esprit du produit : la page y gagne de la visibilité et du trafic.
3. **RSS-Bridge** : à réserver à l'expérimentation (instance sur IP
   résidentielle), en acceptant que ce soit non fiable. Ce n'est pas tant un
   risque juridique (cf. Bright Data, logged-out) qu'un problème de fiabilité
   face aux blocages techniques de Meta.

   *État de l'implémentation (2026-07)* : le fetcher `social.py` est câblé et le
   workflow CI (`refresh.yml`) lance une instance RSS-Bridge **éphémère**
   (service container, tous bridges activés) sur laquelle `RSSBRIDGE_URL` pointe
   par défaut. Mais le runner GitHub est sur **IP de datacenter** — précisément
   ce que Meta bloque : les bridges Facebook/Instagram y échoueront le plus
   souvent (skip + log, aucun crash). C'est donc un branchement *mécanique*, pas
   une collecte fiable. Pour une voie fiable, définir la **variable de dépôt**
   `RSSBRIDGE_URL` vers une instance auto-hébergée sur IP résidentielle : elle a
   priorité sur le service CI. La voie réellement recommandée reste le
   **consentement du propriétaire de la page** (point 2 ci-dessus).

Sources : docs Meta (PPCA, oEmbed Read, référence Graph API Page),
Meta v. Bright Data (N.D. Cal., 23/01/2024), guides développeurs 2026 sur
les APIs Instagram.
