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

## En cours (branche `dev`)

- [x] **Activités permanentes** : découverte OpenStreetMap (musées, monuments,
      parcs d'attraction et aquatiques, cinémas, ludothèques, marchés, fermes,
      curiosités), cadence hebdomadaire découplée du crawl
- [x] Repérage dédié sur les tuiles (badge « Permanent », icône, liseré), sur la
      carte (pastille à icône) et dans la recherche (chips + langage naturel)
- [x] Note d'avis Google ou TripAdvisor, affichée et filtrable (« bien noté »),
      optionnelle — sans clé d'API, les activités sortent sans note
- [x] Tag **✨ Insolite** pour les activités méconnues : heuristique (ni marque,
      ni page wikipédia) confirmée par le LLM, filtrable
- [x] Présentation « donne envie » générée une seule fois à la découverte, et
      lien direct vers le site de l'activité

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
