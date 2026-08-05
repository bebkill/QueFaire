# Roadmap — QueFaire

## v1 — livré

Le socle est en place et tourne en autonomie : deux épicentres publiés,
rafraîchis 2×/jour par la CI, sans intervention.

**Pipeline**
- [x] Collecte multi-sources : RSS, iCal, OpenAgenda, pages HTML via extraction LLM
- [x] Normalisation par règles lisibles (catégorie, public, gratuité) et
      géocodage hors-ligne à la commune
- [x] Modèle **épicentre** : commune de référence + rayon en temps de trajet,
      filtrage à la collecte quel que soit le département, avec le **même
      calcul que le front** pour que collecte et affichage s'accordent
- [x] Table de communes du rayon générée par `build-geo`
- [x] Déduplication inter-sources (on garde la fiche la plus riche)
- [x] Fiches « en clair » : une phrase LLM lève l'ambiguïté des titres obscurs
      (ex. « Faites-vous une terrasse » = dîners en terrasse, pas du bricolage)
- [x] LLM principal + backups en cascade, deux chaînes indépendantes (crawl /
      clarify), bascule sur quota, erreur serveur ou réponse vide
- [x] Cache LLM adressé par contenu et état de fraîcheur des sources,
      **cloisonnés par ville**
- [x] Dégradation gracieuse de bout en bout : aucune indisponibilité ne casse
      un crawl

**Cycle de vie des sources**
- [x] Découverte `discover-oa` (API OpenAgenda) et `discover` (agent LLM),
      toujours en `enabled: false` — validation humaine obligatoire
- [x] Évaluateur d'URL candidate (événements uniques, garde-fous anti-SSRF)
- [x] Circuit complet : découverte hebdo → issues → label `approved` →
      ajout au registre ; formulaire « proposer une source » sur le site
- [x] Retrait automatique des sources sans événement depuis plus de 30 jours

**Site**
- [x] Site statique Astro avec recherche en langage naturel côté client
- [x] Carte des événements (Leaflet/OSM) synchronisée avec les filtres,
      clustering et mise en évidence de la fiche au clic
- [x] Filtre « à moins de X min » (à pied / vélo / voiture), y compris en
      langage naturel, cohérent entre liste et carte
- [x] **Multi-villes** : routes `[city]` générées depuis les données, portail
      `/villes/` avec carte des épicentres, recherche et « me localiser »
- [x] Déploiement multi-villes : un crawl par ville active, un build unique

**Épicentres actifs** : Villemoirieu (nord-Isère) et Pont-de-Salars (Aveyron).

## v1.1 — livré : les activités permanentes

Un agenda ne répond qu'à « qu'est-ce qui se passe ce week-end ». Il manquait
« qu'est-ce qu'on peut faire, tout simplement » — musées, monuments, fermes,
parcs, curiosités : ce qui est vrai toute l'année. **1876 activités** pour
Pont-de-Salars, contre 36 événements datés.

- [x] **Deux fournisseurs complémentaires** : OpenStreetMap (Overpass, 3 miroirs)
      et **DATAtourisme** (Licence Ouverte), indépendants — la panne de l'un
      n'emporte pas l'autre. Flux préféré, API en repli automatique.
- [x] Repérage dédié sur les tuiles (badge « Permanent », icône, liseré), sur la
      carte et dans la recherche (chips + langage naturel), plus un filtre
      **🎪 Événements datés** pour ne voir que l'actualité
- [x] Tag **✨ Insolite**, confirmé par le LLM et non par la seule heuristique,
      et **🏅 Valeurs sûres** adossé aux distinctions officielles
- [x] Présentation « donne envie » générée une seule fois, cache par contenu
- [x] **Page de détail** pour les 716 activités sans site officiel : emplacement
      exact (carte, coordonnées telles que publiées, itinéraire), **photo créditée**
      quand la source en fournit une (Wikimedia Commons, DATAtourisme §8.9), et
      avertissement explicite invitant à vérifier ouverture et maintien en activité
- [x] **Exigence de signal** dans toutes les catégories : une fiche sans
      description, site, horaires, photo ni distinction n'est pas publiée
      (2624 → 1876 fiches). Réversible : une fiche enrichie par sa source
      réapparaît d'elle-même.
- [x] **Commune de départ** saisissable, pour qui refuse la géolocalisation —
      coordonnées embarquées, aucun appel réseau
- [x] Note d'avis Google ou TripAdvisor **collectée mais non affichée** : les CGU
      des deux fournisseurs l'interdisent hors de leurs composants (voir
      `ARCHITECTURE.md`). Remplacée par les distinctions officielles en données
      ouvertes.

## Ensuite

### Court terme

- [ ] Vrai moteur isochrone (OpenRouteService ou Valhalla) à la place de
      l'approximation à vol d'oiseau — pour le filtre front ET le filtre de
      rayon à la collecte (aujourd'hui la même approximation partagée)
- [ ] Densifier les deux épicentres : agendas OpenAgenda de l'est lyonnais (69)
      et de l'ouest de l'Ain (01) pour Villemoirieu, sources Millau et
      Aveyron-Tourisme pour Pont-de-Salars — à valider une par une
- [ ] Soumission directe d'événements : formulaire et/ou adresse mail
      (affiche → extraction LLM), avec file de modération
- [ ] Activités permanentes — suites : parser `opening_hours` pour un filtre
      « ouvert maintenant » et enrichir les activités sans site officiel
- [ ] **Notes d'avis sur les tuiles** (pas sur la carte). Deux voies, toutes deux
      contraintes — voir « Notes d'avis » dans `ARCHITECTURE.md` :
      - *TripAdvisor Content API* : gratuite mais réservée aux partenaires
        approuvés, et impose ses propres bulles + logo + lien retour. Demande une
        candidature puis une refonte de l'affichage de la note ;
      - *fiche embarquée en iframe* (Google Maps Embed API, gratuite et
        illimitée) : la note reste affichée **dans** un composant du fournisseur,
        ce qui règle la question des conditions d'affichage et entretient le
        trafic vers lui. À charger en **façade** — un bouton « Voir la fiche »
        qui n'injecte l'iframe qu'au clic — sinon les cookies tiers imposent une
        bannière de consentement sur tout le site.
- [ ] Fiabiliser la géolocalisation du portail (aujourd'hui l'IP situe les
      postes fixes au nœud régional du fournisseur)

### Moyen terme

- [ ] Retours utilisateurs sur les événements (👍/👎, note) pour apprendre les
      goûts et afficher un **score de match** personnalisé
- [ ] Sources activités outdoor : Décathlon Outdoor, Visorando, Wikiloc
      (balades et itinéraires — pas des événements datés : premier cas d'usage
      du schéma `Place`, avec notes et avis existants pour « bien noté »)
- [ ] Nouveaux épicentres — le coût marginal est désormais un fichier YAML
      plus un `build-geo`

### Long terme

- [ ] **Préférences utilisateur sans compte** — plutôt qu'un compte (inscription,
      mots de passe, base de données, obligations RGPD, suppression sur demande),
      l'utilisateur reste propriétaire de ses préférences :
      - persistance locale par défaut (`localStorage`) : catégories favorites,
        commune de départ, rayon habituel, activités déjà faites. Première
        partie, purement fonctionnelle, aucune donnée ne quitte le navigateur ;
      - bouton **« Exporter mes préférences »** → un fichier JSON que
        l'utilisateur garde, et **« Importer »** pour le recharger sur un autre
        appareil ou après vidage du navigateur.

      L'intérêt dépasse la simplicité juridique : sans serveur ni base, ça reste
      cohérent avec un site 100 % statique, et il n'y a **aucune donnée
      personnelle à protéger puisqu'on n'en détient aucune**. Le fichier devient
      aussi un format d'échange (partager un profil « sorties en famille »).
      Points à traiter : versionner le schéma du fichier pour rester
      rétrocompatible, valider à l'import (ne jamais faire confiance au contenu),
      et rester utilisable sans préférences du tout.
- [ ] Contributions et activités réalisées, adossées au même fichier
- [ ] Extension aux professionnels et commerçants (« je cherche un électricien »,
      « un tailleur de pierre ») : même pipeline, schéma `Place`, même recherche

### Confiance dans l'existence des activités

On publie ce qu'OSM et DATAtourisme déclarent, sans rien savoir de leur
existence réelle. Vécu : « Accrobranche » définitivement fermé, toujours en
ligne. Aujourd'hui le seul signal est une absence de 14 jours
(`RETENTION_DAYS`), et il ne se déclenche que si la source retire la fiche — ce
que les offices de tourisme ne font pas toujours.

Le patron existe déjà dans le projet : `health.py` désactive une source qui ne
produit plus, **sans jamais supprimer la ligne**, et un humain peut revenir en
arrière. C'est ce modèle qu'il faut décliner sur les activités.

**Vérification automatique**, par ordre de coût croissant :

- [ ] **Le site officiel répond-il ?** 1233 fiches sur ~2100 ont une URL. Une
      requête HEAD par fiche, mensuelle, à travers le garde-fou anti-SSRF déjà
      écrit (`security.py`). Un site mort n'est pas une preuve de fermeture, mais
      c'est un signal fort et gratuit. À croiser, jamais à appliquer seul.
- [ ] **`businessStatus` de Google Places** (`OPERATIONAL`,
      `CLOSED_TEMPORARILY`, `CLOSED_PERMANENTLY`) : c'est *exactement* le champ
      cherché, et ce n'est pas un avis. Deux réserves à traiter : l'API est
      payante au-delà du palier gratuit, et Google interdit de conserver ses
      données au-delà de 30 jours (hors `place_id`). Donc utilisable comme
      **filtre transitoire re-vérifié**, pas comme donnée stockée dans
      `places.json`.
- [ ] **Recoupement inter-fournisseurs** : une fiche que DATAtourisme retire
      alors qu'OSM la garde (ou l'inverse) mérite un examen. Gratuit, on a déjà
      les deux flux et la provenance par tag.

**Vote des utilisateurs** — 👍 top / 👌 correct / 👎 nul / ❌ n'existe plus.
L'idée est juste, mais trois obstacles doivent être réglés AVANT d'écrire une
ligne, sinon on livre une faille :

- [ ] **Il n'y a pas de serveur pour recevoir les votes.** Le site est
      statique. Forme envisagée, cohérente avec l'existant : une fonction
      *serverless* reçoit le vote, un bot agrège dans un JSON committé — exactement
      ce que fait `refresh.yml` pour les données. Les votes redeviennent de la
      donnée statique au build suivant.
- [ ] **❌ ne doit JAMAIS supprimer automatiquement.** Un vote anonyme qui
      supprime, c'est une primitive de suppression offerte à n'importe qui : un
      script vide le catalogue, un concurrent efface un rival. La règle doit être
      celle de `health.py` — un seuil **masque** (drapeau réversible), il ne
      supprime pas, et la fiche revient du fournisseur si le signalement était
      faux. Exiger une corroboration : plusieurs signalements indépendants, ou un
      signalement **plus** un site officiel mort.
- [ ] **« Anonyme » n'est pas gratuit.** Sans clé de déduplication, le vote est
      bourrable en une ligne de script ; avec une clé (hash d'IP, empreinte de
      navigateur), on traite une donnée personnelle pseudonymisée — donc du RGPD,
      ce qu'on cherchait à éviter. Voie défendable : limitation de débit **au
      bord** (Cloudflare Turnstile, sans cookie ni stockage d'IP) et n'agréger
      que des compteurs, sans conserver aucun identifiant. À écrire noir sur blanc
      dans la page « à propos ».
- [ ] Une fois ces trois points réglés : statistiques, classement par
      satisfaction, et alimentation du **score de match** déjà en roadmap.

**Renvoyer les corrections aux sources** — la partie où il faut être lucide :

- [ ] **OpenStreetMap : tractable.** L'API Notes accepte la création d'une note
      géolocalisée sans authentification. Un ❌ corroboré peut donc ouvrir une
      note « signalé comme définitivement fermé par les visiteurs de QueFaire,
      à vérifier sur place » — que la communauté OSM traite avec ses propres
      règles. C'est une contribution réelle, et le bon usage : on signale, on ne
      modifie pas la base d'autrui.
- [ ] **DATAtourisme : pas d'API de signalement.** La plateforme agrège, elle ne
      possède pas la donnée : le producteur est l'office de tourisme identifiable
      sur la fiche. Il n'existe pas d'API pour lui écrire. Réaliste : une page
      publique listant les signalements par producteur, plus un envoi groupé par
      courriel. Boucle manuelle et lente, à ne pas survendre — mais elle a de la
      valeur pour eux, et c'est un argument de partenariat.

### Recherche par intention (LLM)

Aujourd'hui la barre de recherche est un parseur de règles français
(`nlsearch.js`) : instantané, hors-ligne, et il couvre bien ce qui est fait de
dates, de catégories, de distance et de prix. Il ne comprend pas
« un truc à faire sous la pluie avec un enfant de quatre ans ».

**Faut-il une base vectorielle / un RAG ?** Non — et il faut séparer deux
problèmes qu'on a tendance à confondre :

1. **Comprendre la requête** → en extraire un filtre structuré (catégories,
   public, intérieur/extérieur, temps de trajet). C'est là qu'est presque toute
   la valeur, et ça ne demande aucun RAG : notre corpus est **déjà structuré**.
   « à moins de 30 minutes », « gratuit », « en intérieur » sont des FAITS, pas
   des similarités — un filtre exact sur 2000 fiches typées battra la similarité
   sémantique, qui est mauvaise sur la négation, les nombres et les distances.
2. **Classer le corpus** sémantiquement sur le texte libre résiduel
   (« insolite pour impressionner mes beaux-parents »). C'est la queue de
   distribution, et la seule part où un plongement apporterait quelque chose.

Et **jamais de base vectorielle** au sens Pinecone/Qdrant/pgvector : ces outils
paient leur coût à partir de 10⁵–10⁸ vecteurs. À 2000 fiches, un index tient
dans ~0,8 Mo quantifié en int8, et 2000 produits scalaires en JavaScript sont
plus rapides que l'aller-retour réseau vers la base. Si plongements il y a, ce
sera **un fichier statique de plus**, pas un service.

Ordre de valeur décroissante :

- [ ] **Récolter des facettes au build, avec le LLM qu'on paie déjà.** `present()`
      lit déjà chaque activité pour en écrire le `tldr` : lui demander au même
      appel d'émettre ce que les règles ne savent pas déduire —
      intérieur/extérieur, sensible à la météo, tranche d'âge, durée typique,
      effort physique, « romantique », « impressionne » — transforme la
      compréhension du LLM en **données statiques**, cherchables hors-ligne, sans
      coût à l'exécution. Le cache adressé par contenu fait que ça ne se paie
      qu'une fois par fiche. C'est le meilleur rapport valeur/complexité de toute
      cette section, et ça n'exige aucune infrastructure nouvelle.
- [ ] **Extraction d'intention à l'exécution.** Elle suppose un secret d'API,
      donc un bout de serveur : une fonction *serverless* (Cloudflare Worker,
      ~30 lignes, gratuite à ce volume) qui prend la phrase et rend le filtre
      structuré. Le site reste statique, seule la barre de recherche appelle
      dehors. **Le parseur de règles reste le chemin par défaut et le repli** :
      fonction absente, en panne ou non configurée, la recherche marche encore —
      c'est la dégradation gracieuse appliquée partout ailleurs dans le projet.

#### Faire tourner le modèle ailleurs que chez un fournisseur

Deux pistes envisagées, dont une seule tient.

**Un petit LLM sur GitHub Actions : pas pour l'intention.** Actions tourne au
*build*, pas à la requête — il ne peut pas répondre à quelqu'un qui tape dans la
barre de recherche. Le raisonnement vaut en revanche pour le travail de build
(facettes, présentations) : un modèle 1–3 B quantifié via llama.cpp tient sur un
runner (4 vCPU, 16 Go, **pas de GPU**), mais à quelques jetons par seconde sur
CPU, les ~2000 extractions passeraient de 100 secondes à plusieurs heures — et le
job Actions est plafonné à 6 h. Piste réelle mais mauvaise affaire, **sauf comme
dernier maillon de la chaîne de backups** quand tous les quotas d'API sont morts :
là, lent et gratuit vaut mieux que rien.

**Un modèle dans le navigateur : la bonne réponse, mal dimensionnée si on prend
un LLM génératif.** Le raisonnement sur la confidentialité est juste et c'est ce
qui rend la piste séduisante : rien ne sort de l'appareil, donc aucun secret à
protéger, aucune donnée personnelle traitée, aucune obligation RGPD. Mais :

- un LLM génératif utile pèse **plusieurs centaines de Mo** en 4 bits, même à
  0,5–1,5 B de paramètres. Sur une page qui fait aujourd'hui 0,5 Mo, c'est
  disqualifiant pour un visiteur venu chercher une idée de sortie dimanche ;
- WebGPU n'est pas universel, en particulier sur mobile — et le mobile est
  précisément l'appareil de l'utilisateur en vadrouille ;
- l'inférence *distribuée entre* appareils (façon Petals) est à écarter : elle
  suppose des pairs connectés au même instant et donne une latence incompatible
  avec une barre de recherche. « Distribué » ici doit vouloir dire **local sur
  l'appareil de chacun**, pas réparti entre les visiteurs.

- [ ] **La forme viable : un petit modèle de tâche, pas un LLM.** Extraire une
      intention sur un vocabulaire FERMÉ (14 catégories, une dizaine de facettes,
      dates, distances) est de la classification et du remplissage de champs, pas
      de la génération. Un encodeur distillé de classe MiniLM en ONNX quantifié
      pèse quelques dizaines de Mo, tourne en WASM **sans exiger WebGPU**, et
      suffit à projeter une phrase sur nos facettes. C'est un à deux ordres de
      grandeur sous un LLM génératif, pour le même service ici.
- [ ] **Et à activer explicitement.** Chargement à la demande derrière un
      interrupteur (« recherche intelligente — téléchargement unique »), le
      parseur de règles restant le défaut. Le choix appartient à l'utilisateur et
      se mémorise dans son fichier de préférences, comme le reste.

À noter : si les **facettes récoltées au build** (point précédent) sont en place,
le navigateur n'a plus qu'à faire correspondre une phrase à des facettes déjà
calculées. Le besoin en modèle embarqué s'effondre — raison de plus pour traiter
les facettes d'abord.
- [ ] **Plongements, seulement si la mesure les justifie.** Précalculés par le
      pipeline (cache par hash de contenu), livrés en fichier quantifié par ville,
      parcourus en force brute dans le navigateur. Reste à trancher : le
      plongement de la REQUÊTE. Soit le Worker ci-dessus le calcule aussi, soit
      un modèle embarqué (transformers.js) — mais 20 à 30 Mo au premier
      chargement, ce qui est disqualifiant pour une page qui pèse 0,5 Mo.
      À ne lancer qu'avec un jeu de requêtes réelles pour mesurer si le gain
      existe.

### Moteur de visite (séjour jour par jour)

L'utilisateur indique une durée de séjour, ses goûts et ses contraintes ; le
moteur propose un itinéraire jour par jour, détaillé, qu'il peut sauvegarder ou
imprimer. Sur chaque proposition il vote 👍/👎 ; on garde les 👍, on remplace les
👎, et un champ libre permet de dire *ce qui ne va pas* (« trop de châteaux »,
« trop loin le matin »). On apprend des rejets autant que des validations.

C'est la brique qui donne son sens à tout le reste : le catalogue devient un
outil de décision et non un annuaire.

- [ ] **Prérequis bloquant : les horaires.** 70 fiches sur ~2000 portent des
      horaires exploitables. Un itinéraire jour par jour sans savoir les jours de
      fermeture envoie les gens devant une porte close, avec l'aplomb d'un
      programme imprimé. Il faut d'abord monter la couverture (parsing
      `opening_hours`, champ `openingHoursSpecification` de DATAtourisme,
      enrichissement des fiches sans site) — sinon le moteur produit des
      absurdités confiantes, ce qui est pire que pas de moteur.
- [ ] **Temps de trajet entre deux activités**, et non depuis l'épicentre : c'est
      une matrice 300×300, calculable dans le navigateur avec la fonction
      existante. Le vrai moteur isochrone (déjà en court terme) la rendrait juste.
- [ ] **Composition du séjour** : regrouper par proximité géographique dans la
      journée, alterner les registres (patrimoine / nature / gourmand), respecter
      une durée de journée plausible, placer les incontournables tôt dans le
      séjour (météo, fermeture imprévue).
- [ ] **Boucle de vote.** Les 👍/👎 et le texte libre alimentent le **fichier de
      préférences** décrit plus haut : pas de compte, pas de base, pas de RGPD, et
      l'utilisateur retrouve ses recherches sans tout refaire. C'est la même
      brique, et c'est ce qui rend la sauvegarde utile plutôt que gadget.
- [ ] **Impression** : feuille de style dédiée (une journée par page, carte
      statique, adresses et horaires en clair, pas de navigation).
- [ ] Reformulation LLM du texte de rejet en contraintes exploitables — même
      remarque que pour la recherche par intention : ça suppose la fonction
      *serverless*, et le moteur doit rester utilisable sans elle (le vote seul
      suffit à réorienter les propositions).

## Écarté

- **Réseaux sociaux (Facebook, Instagram)** — implémenté puis retiré : pas
  d'API publique de lecture chez Meta, contournement RSS-Bridge inopérant
  depuis les IP de datacenter de la CI, et qualité d'extraction douteuse
  (un post n'est pas un événement structuré). Raisonnement détaillé dans
  [`ARCHITECTURE.md`](ARCHITECTURE.md#pistes-écartées).
