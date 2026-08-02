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

## Écarté

- **Réseaux sociaux (Facebook, Instagram)** — implémenté puis retiré : pas
  d'API publique de lecture chez Meta, contournement RSS-Bridge inopérant
  depuis les IP de datacenter de la CI, et qualité d'extraction douteuse
  (un post n'est pas un événement structuré). Raisonnement détaillé dans
  [`ARCHITECTURE.md`](ARCHITECTURE.md#pistes-écartées).
