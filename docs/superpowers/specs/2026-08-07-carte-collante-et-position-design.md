# Carte collante et position du visiteur — design

> Statut : validé le 2026-08-07. Prochaine étape : plan d'implémentation.

## Le problème

Deux manques, liés par le même besoin — situer une activité **par rapport à soi**.

1. **La carte disparaît dès qu'on lit la liste.** `#mapwrap` est un bloc ordinaire
   posé entre la barre de résultats et les grilles : on l'ouvre, on voit les
   points, on descend lire les tuiles, et la carte est partie. Le rapprochement
   « cette activité est là, moi je suis ici » ne peut se faire que de mémoire.

2. **Il n'existe aucun moyen de dire « montre-moi où je suis ».** Un point bleu
   « Vous êtes ici » existe pourtant déjà, mais uniquement comme effet de bord :
   il n'apparaît que si un filtre exige la distance (« Près de moi », « Trajet
   max », tri par distance) ou si une commune de départ a été saisie. Le visiteur
   qui veut simplement se voir sur la carte doit poser un filtre dont il n'a que
   faire.

## Ce qu'on construit

- La carte devient un **bandeau collant** sous la barre de recherche : les tuiles
  défilent dessous, elle reste à l'écran.
- Un **bouton 📍** en surimpression sur la carte pose la position — géolocalisation
  du navigateur, ou repli sur la commune saisie dans les réglages.
- **Survoler une tuile** met en évidence son point sur la carte, et l'y ramène
  s'il est hors cadre.
- Poser une position suffit à faire apparaître **le temps de trajet sur les
  tuiles**, sans qu'il faille poser un filtre.
- La **commune** de départ est mémorisée d'une visite à l'autre. Jamais les
  coordonnées GPS.

## Décisions, et pourquoi

### Bandeau collant plutôt que colonne latérale

Une vue scindée façon Booking (liste à gauche, carte à droite) est la forme
canonique, mais elle impose deux mises en page très différentes selon la largeur,
alors que la page en a **déjà** deux (colonne de réglages ancrée au-delà de
1040 px, en rideau en dessous). Un bandeau collant se comporte pareil partout et
n'ajoute aucun régime. Le coût : de la hauteur, sur téléphone surtout — d'où une
carte nettement plus basse qu'aujourd'hui, et la bascule 🗺️ qui reste elle-même
collante, donc à un geste.

### Le bouton, plutôt que l'automatique

Demander la position dès l'ouverture de la carte ferait surgir une boîte de
dialogue du navigateur que personne n'a réclamée. Un geste explicite est aussi la
seule forme cohérente avec la ligne du projet — aucune donnée ne quitte le
navigateur, et rien ne se déclenche dans le dos du visiteur.

### Le marqueur ne doit pas mentir

`requestGeoloc()` retombe aujourd'hui sur le **centre du secteur** quand la
position est refusée. C'est un compromis acceptable pour estimer un temps de
trajet — c'est un mensonge sous un marqueur « Vous êtes ici ». La position cesse
donc d'être un simple `{lat, lon}` pour devenir une origine qui **dit d'où elle
vient**, et le marqueur n'est dessiné que lorsque cette provenance est réelle.

### La commune, pas les coordonnées

Le navigateur mémorise déjà l'autorisation de géolocalisation : un clic sur 📍
redonne la position précise instantanément. Écrire un point GPS exact sur le
disque du visiteur n'achèterait donc qu'un clic, au prix d'une donnée sensible
persistée. Un nom de commune, lui, est ce que le visiteur a tapé lui-même.

## Architecture

`index.astro` fait 1396 lignes, dont ~230 de Leaflet. Y empiler la carte
collante, le bouton et le survol le rendrait impraticable. Deux modules sont
extraits — strictement le code qu'on touche, aucun remaniement opportuniste.

| Fichier | Rôle |
|---|---|
| `site/src/lib/carte.js` *(nouveau)* | Tout Leaflet. Ne connaît ni secteur, ni catégorie, ni filtre. |
| `site/src/lib/origine.js` *(nouveau)* | Résolution du point de départ : géoloc, commune, mémoire. Sans état caché. |
| `site/src/pages/[city]/index.astro` | Filtres, tri, pagination, liste — et l'orchestration des deux modules. |
| `site/src/styles/global.css` | Bandeau collant, bouton 📍, états de marqueur. |

### `carte.js`

```js
creerCarte({ el, centre, onSelection }) → {
  afficher(items, { recadrer }),   // remplace tous les points
  poserOrigine(origine | null),    // null retire le marqueur
  centrerSurOrigine(),
  survoler(entree | null),         // met en évidence, recadre si hors champ
  redimensionner(),                // invalidateSize()
}
```

Un `item` porte tout ce dont la carte a besoin, **déjà cuit** par l'appelant :

```js
{ entree, lat, lon, isPlace, unusual, emoji, color, title, href, tooltip /* HTML */ }
```

C'est la frontière qui rend le module autonome : les libellés de catégorie, les
émojis et les horaires d'ouverture restent l'affaire de la page, qui fabrique
l'infobulle. `carte.js` ne fait que placer, grouper, mettre en évidence.

`onSelection(entrees)` est le lien existant carte → liste (clic sur un point ou
sur une entrée de bulle) ; la page y branche son `selectCards`, inchangé.

### `origine.js`

Des fonctions pures, ou presque — aucun état de module :

```js
chercherCommune(communePoints, saisie) → { lat, lon, nom } | null
geolocaliser() → Promise<{ lat, lon, precision }>   // rejette avec un code
memoriser(ville, nomCommune) / restaurer(ville) → nom | null / oublier()
```

La page assemble l'origine courante :

```js
origine = { lat, lon, source: 'geoloc' | 'commune' | 'defaut', libelle, precision }
```

- `geoloc` — point bleu, cercle de précision, popup « Vous êtes ici ».
- `commune` — épingle 🏠, popup « Départ : Crémieu (centre de la commune) ».
- `defaut` — centre du secteur, **aucun marqueur** : la page appelle alors
  `poserOrigine(null)`. Sert seulement à ne pas priver du filtre « trajet max »
  quelqu'un qui a refusé la géolocalisation, et le message existant continue de
  le dire. `carte.js` n'a donc jamais à connaître le cas `defaut` — il reçoit une
  origine à afficher, ou rien.

`chercherCommune` reprend telle quelle la logique de `setOrigin` (normalisation
par `fold`, correspondance exacte puis par préfixe) ; `fold` vient de
`nlsearch.js`. La table `commune_points` est embarquée dans `sector.json` :
aucun appel réseau, comme aujourd'hui.

## Comportements

### Le bandeau

`#mapwrap` reste à sa place dans le DOM et devient `position: sticky`, ancré sous
`.explore-top`.

Les hauteurs sont **mesurées, pas supposées** — la page le fait déjà pour
`--header-h` avec un `ResizeObserver`, parce que l'en-tête triple de hauteur sur
téléphone. On étend le même mécanisme :

- `--top-h` — hauteur de `.explore-top`. `#mapwrap` colle à
  `calc(var(--header-h) + var(--top-h))`.
- `--sticky-h` — hauteur totale de ce qui est collé : en-tête + barre + carte si
  elle est affichée. Sert de `scroll-margin-top` (+ 16 px) aux `.section` et aux
  `.card`. Sans elle, « aller à la page 3 » ou un clic sur un point feraient
  défiler la cible **derrière** la carte. Recalculée par les mêmes
  `ResizeObserver`, **et à chaque bascule 🗺️** — fermer la carte doit rendre au
  défilement les 300 px qu'elle réservait.

Hauteur de la carte : `clamp(180px, 34vh, 300px)`, contre 420 px fixes
aujourd'hui — un bandeau permanent ne peut pas prendre la moitié de l'écran.
Sous 720 px de large : `clamp(150px, 28vh, 220px)`. Sous 560 px de haut :
150 px.

Deux effets qui ne se voient qu'une fois en place :

- `#mapwrap` prend un fond opaque et le même artifice de `box-shadow` latéral que
  `.explore-top`, sinon les tuiles se voient en transparence sous la note de bas
  de carte pendant le défilement.
- Un `ResizeObserver` sur `#map` rappelle `redimensionner()` à chaque changement
  de hauteur — Leaflet ne le voit pas seul et laisse des tuiles grises.

`z-index` : `#mapwrap` à 14, sous `.explore-top` (15) et au-dessus des tuiles.
`#map` garde son `z-index: 1`, qui lui crée un contexte d'empilement et contient
les couches internes de Leaflet.

### Le bouton 📍

Posé en surimpression sur `#mapwrap` (bouton HTML, pas un contrôle Leaflet :
il doit rester stylable avec le reste de la page et atteignable au clavier dans
l'ordre du document).

| État | Aspect | Clic |
|---|---|---|
| repos (aucune origine) | 📍 « Ma position » | demande la géolocalisation |
| attente | désactivé, `aria-busy`, « Localisation… » | — |
| origine `commune` | 📍 actif, titre « Utiliser ma position réelle plutôt que Crémieu » | demande la géolocalisation |
| origine `geoloc` | 📍 actif (`aria-pressed`) | recentre la carte sur le point |

Échec (refus, indisponibilité, délai dépassé) : message sous la carte, « Position
refusée — indiquez votre commune dans ☰ Réglages », le panneau s'ouvre et le
focus va sur le champ « Point de départ ». **Une origine `commune` déjà posée est
conservée** : un échec ne fait jamais perdre ce qu'on avait.

**Précision aberrante.** Si `coords.accuracy` dépasse 5 km, c'est une position
par IP — souvent le nœud régional du fournisseur d'accès. On la traite comme un
échec, avec son propre message (« position trop imprécise — indiquez votre
commune »), plutôt que de poser un point bleu faux à 40 km. En deçà, le cercle de
précision est dessiné : il dit lui-même ce que le point vaut.

**Passage de `commune` à `geoloc`.** Le champ « Point de départ » est vidé, sa
pastille de filtre actif disparaît, et la mémoire est effacée avec lui — règle
simple et prévisible : *la mémoire est le reflet du champ*.

### Survol tuile → carte

Écoute déléguée sur les deux grilles : `pointerover` / `pointerout` (ignorés si
`pointerType === 'touch'` — le survol n'existe pas au doigt, et une tape navigue
déjà) et `focusin` / `focusout` pour le clavier.

`rendre(sec)` alimente un `WeakMap` élément → entrée au moment où il fabrique la
page ; `carte.survoler(entree)` retrouve le marqueur par un `Map` entrée →
marqueur tenu par `afficher()`. Les points groupés pointent vers le marqueur de
leur cluster : survoler une tuile prise dans un amas met l'amas en évidence,
faute de mieux.

Mise en évidence : rayon et épaisseur augmentés pour un `circleMarker`
d'événement, classe `is-hover` sur l'élément d'une pastille d'activité ou de
cluster, et passage au premier plan.

Recadrage : si le point est hors de `map.getBounds().pad(-0.15)`, `panTo`. Pas de
recadrage pendant que le visiteur fait glisser la carte ; pas d'animation sous
`prefers-reduced-motion`. Anti-rebond de 80 ms — sans lui, un passage de souris
sur une rangée déclenche six recadrages.

Le lien inverse existant (clic sur un point → la tuile se surligne, change de
page si besoin, et défile) est **conservé sans modification**.

### Temps de trajet sans filtre

Le calcul distance/temps est aujourd'hui conditionné à `needsGeo` (« Près de
moi », trajet max, tri distance). Il se déclenche désormais dès qu'une origine
existe, quelle qu'en soit la provenance. `needsGeo` ne sert plus qu'à décider
s'il faut **réclamer** une position au visiteur qui n'en a pas donné.

Conséquence voulue : cliquer 📍 fait apparaître « ≈ 12 min en voiture » sur toutes
les tuiles, sans rien filtrer. Le filtrage par `maxMinutes`, lui, reste
strictement conditionné au filtre — poser une position ne doit jamais masquer
quoi que ce soit.

Les fiches sans coordonnées gardent leur comportement actuel : distance vide, et
exclusion **seulement** si un `maxMinutes` est demandé.

### Recadrage et origine lointaine

L'origine entre dans le `fitBounds` uniquement si elle tombe dans les limites du
secteur élargies d'une marge. Quelqu'un qui consulte Villemoirieu depuis Paris
doit voir le nord-Isère, pas une carte de France centrée sur le vide ; son
marqueur est posé, simplement hors cadre initial.

### Mémoire

Clé `quefaire:origin:v1`, valeur `{ ville: "<id>", commune: "<nom>" }`.

- Restaurée au chargement **seulement si `ville` correspond à la page courante** —
  une commune du Lévézou n'a aucun sens dans le nord-Isère.
- Elle remplit le champ, pose le marqueur, active les temps de trajet. Elle ne
  pose **aucun filtre** : on restaure un point de départ, pas une recherche.
- `try/catch` autour de `localStorage` sur le modèle de `prefs.js` — stockage
  refusé (navigation privée, réglage strict) : on repart de zéro, jamais de page
  cassée.
- « Tout réafficher » et le retrait de la pastille `🏠` effacent la clé.

## Vérification

`npm run build` doit passer. Puis une passe Playwright sur `/villemoirieu/`
(`playwright-core` est déjà en dépendance de dev ; le dépôt n'a pas de harnais de
test, ces contrôles sont donc à faire à la main ou par script jetable) :

1. Carte ouverte, défiler jusqu'aux dernières tuiles : la carte reste visible et
   ne recouvre ni la barre de recherche ni les titres de section.
2. Géolocalisation refusée : **aucun** marqueur de position n'apparaît, le
   message renvoie vers le champ « Point de départ », lequel prend le focus.
3. Saisir « Crémieu » : épingle 🏠 sur la carte, temps de trajet sur les tuiles
   sans qu'aucun filtre soit actif, pastille `🏠 Crémieu` près du compteur.
4. Recharger : Crémieu est toujours là, aucun filtre n'est actif.
5. Survoler une tuile hors cadre : la carte s'y déplace, le bon point grossit.
6. Cliquer un point : la bonne tuile se surligne et défile **sous** la carte, pas
   derrière.
7. À 390 px de large : la carte ne prend pas plus de ~28 % de la hauteur, la
   bascule 🗺️ reste atteignable.
8. Changer la largeur autour de 1040 px, ouvrir/fermer la colonne de réglages :
   pas de tuile grise (`redimensionner()` bien appelé).

## Hors périmètre

- **Mémoriser l'état ouvert/fermé de la carte.** La bascule 🗺️ est collante, donc
  toujours à un geste ; s'en souvenir n'apprendrait rien de plus.
- **Itinéraire réel.** Déjà en roadmap. La note « à vol d'oiseau corrigé » sous la
  carte reste affichée telle quelle.
- **Vue scindée liste/carte.** Écartée au profit du bandeau (voir *Décisions*).
- **Notes d'avis sur la carte.** Interdites par les conditions d'utilisation dans
  notre configuration ; rien ne change ici.
