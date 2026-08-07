# Carte collante et position du visiteur — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rendre la carte collante pendant le défilement des tuiles et permettre au visiteur d'y voir sa propre position — géolocalisée ou nommée — pour situer chaque activité par rapport à lui.

**Architecture:** Le code Leaflet (~230 lignes) et la résolution du point de départ sortent de `site/src/pages/[city]/index.astro` (1396 lignes) vers deux modules autonomes, `site/src/lib/carte.js` et `site/src/lib/origine.js`. La page garde les filtres, le tri, la pagination et la liste, et orchestre les deux. `#mapwrap` devient `position: sticky` sous la barre de recherche, les hauteurs collantes étant mesurées par `ResizeObserver` et publiées en variables CSS.

**Tech Stack:** Astro 5 (site statique), Leaflet 1.9 (importé dynamiquement), JavaScript ESM sans transpilation, CSS écrit à la main dans `site/src/styles/global.css`. Tests unitaires avec le lanceur intégré de Node (`node --test`) — aucune dépendance ajoutée.

**Spec de référence :** [`docs/superpowers/specs/2026-08-07-carte-collante-et-position-design.md`](../specs/2026-08-07-carte-collante-et-position-design.md)

## Global Constraints

- **Langue.** Tout le code de ce dépôt est écrit en français : noms de fonctions, de variables, commentaires, messages. `creerCarte`, `poserOrigine`, `survoler` — pas `createMap`, `setOrigin`, `hover`. Les identifiants HTML/CSS existants restent tels quels (`#mapwrap`, `#map`, `.card`).
- **Commentaires.** Le dépôt commente le **pourquoi**, jamais le quoi, et souvent sur plusieurs lignes en expliquant l'alternative écartée. Les blocs de commentaires donnés dans ce plan sont à recopier tels quels : ils font partie du livrable.
- **Aucune nouvelle dépendance.** `site/package.json` ne doit gagner que le script `test`. Pas de framework de test, pas de plugin Leaflet.
- **Rien ne quitte le navigateur.** Aucun appel réseau nouveau. La table `commune_points` est déjà embarquée dans `sector.json` (585 entrées pour Villemoirieu).
- **`localStorage` est toujours faillible.** Tout accès est enveloppé d'un `try/catch` qui retombe silencieusement sur un comportement dégradé, jamais sur une page cassée — modèle : `site/src/lib/prefs.js:37-56`.
- **Aucune coordonnée GPS persistée.** Seul un nom de commune peut être écrit dans `localStorage`.
- **Le marqueur de position ne ment pas.** Une origine de source `defaut` (centre du secteur) ne donne jamais de marqueur.
- **Les filtres ne se posent pas tout seuls.** Poser une position active l'affichage des temps de trajet ; elle ne doit jamais masquer une fiche ni activer un chip.
- **Vérification à chaque tâche :** `npm run build` depuis `site/` doit passer. Les tâches avec tests ajoutent `npm test`.

---

## File Structure

| Fichier | Responsabilité | Tâches |
|---|---|---|
| `site/src/lib/origine.js` *(créé)* | Résoudre un point de départ : recherche de commune, géolocalisation, mémoire. Fonctions sans état de module. | 1 |
| `site/tests/origine.test.js` *(créé)* | Tests unitaires de `origine.js`. | 1 |
| `site/src/lib/carte.js` *(créé)* | Tout Leaflet : création, groupement, marqueurs, infobulles, marqueur d'origine, mise en évidence. Ignore tout du catalogue. | 2, 3 |
| `site/tests/carte.test.js` *(créé)* | Tests unitaires de `grouperPoints` (fonction pure). | 2 |
| `site/package.json` | Script `test`. | 1 |
| `site/src/pages/[city]/index.astro` | Filtres, tri, pagination, liste, et orchestration des deux modules. | 3-8 |
| `site/src/styles/global.css` | Bandeau collant, bouton 📍, marqueurs d'origine, états de survol. | 4, 5, 7 |
| `docs/ROADMAP.md` | Trace de la fonctionnalité livrée. | 9 |

**Ordre des tâches et dépendances :** 1 et 2 sont indépendantes l'une de l'autre. 3 dépend de 2. 4 est indépendante (CSS + mesures). 5 dépend de 1 et 3. 6 dépend de 5. 7 dépend de 3. 8 dépend de 5. 9 dépend de tout.

---

## Task 1: Module `origine.js` et harnais de test

**Files:**
- Create: `site/src/lib/origine.js`
- Create: `site/tests/origine.test.js`
- Modify: `site/package.json` (ajout du script `test`)

**Interfaces:**
- Consumes: `fold` depuis `site/src/lib/nlsearch.js` (signature : `fold(s: string) → string` — supprime accents, passe en minuscules, remplace apostrophes et tirets par des espaces).
- Produces:
  - `chercherCommune(communePoints, saisie) → { nom, lat, lon } | null`
  - `geolocaliser({ geo }?) → Promise<{ lat, lon, precision }>` — rejette avec une `Error` portant `.code` valant `'indisponible' | 'refusee' | 'imprecise'`
  - `memoriser(ville: string, commune: string) → void`
  - `restaurer(ville: string) → string | null`
  - `oublier() → void`
  - `PRECISION_MAX_M = 5000`, `CLE = 'quefaire:origin:v1'`

- [ ] **Step 1 : Ajouter le script de test**

Dans `site/package.json`, ajouter la ligne `test` dans `scripts` (les autres lignes sont inchangées) :

```json
  "scripts": {
    "dev": "astro dev",
    "build": "astro build",
    "preview": "astro preview",
    "test": "node --test tests/"
  },
```

Le lanceur est celui intégré à Node (v24 en place) : aucune dépendance ajoutée, ce qui est la contrainte du dépôt.

- [ ] **Step 2 : Écrire les tests qui échouent**

Créer `site/tests/origine.test.js` :

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  CLE, PRECISION_MAX_M, chercherCommune, geolocaliser, memoriser, oublier, restaurer,
} from '../src/lib/origine.js';

const COMMUNES = [
  { nom: 'Crémieu', lat: 45.7228, lon: 5.2503 },
  { nom: 'Saint-Chef', lat: 45.6997, lon: 5.3661 },
  { nom: 'Saint-Chef-le-Haut', lat: 45.7002, lon: 5.3702 },
];

test('chercherCommune ignore accents et casse', () => {
  assert.equal(chercherCommune(COMMUNES, 'cremieu').nom, 'Crémieu');
  assert.equal(chercherCommune(COMMUNES, '  CRÉMIEU ').nom, 'Crémieu');
});

test('chercherCommune traite le tiret comme une espace', () => {
  assert.equal(chercherCommune(COMMUNES, 'saint chef').nom, 'Saint-Chef');
});

test('chercherCommune accepte un préfixe', () => {
  assert.equal(chercherCommune(COMMUNES, 'crem').nom, 'Crémieu');
});

test('chercherCommune préfère la correspondance exacte au préfixe', () => {
  // Sans cette priorité, « Saint-Chef » renverrait « Saint-Chef-le-Haut » selon
  // l'ordre du tableau : le visiteur qui tape le nom entier doit obtenir ce nom.
  assert.equal(chercherCommune(COMMUNES, 'Saint-Chef').nom, 'Saint-Chef');
});

test('chercherCommune rend null sur une commune inconnue ou une saisie vide', () => {
  assert.equal(chercherCommune(COMMUNES, 'Marseille'), null);
  assert.equal(chercherCommune(COMMUNES, '   '), null);
  assert.equal(chercherCommune(COMMUNES, ''), null);
  assert.equal(chercherCommune(null, 'crem'), null);
});

const geoQuiRend = (coords) => ({
  getCurrentPosition: (ok) => ok({ coords }),
});
const geoQuiRefuse = { getCurrentPosition: (ok, ko) => ko({ code: 1 }) };

test('geolocaliser rend la position et sa précision', async () => {
  const p = await geolocaliser({
    geo: geoQuiRend({ latitude: 45.72, longitude: 5.24, accuracy: 18 }),
  });
  assert.deepEqual(p, { lat: 45.72, lon: 5.24, precision: 18 });
});

test('geolocaliser rejette un refus avec le code « refusee »', async () => {
  await assert.rejects(() => geolocaliser({ geo: geoQuiRefuse }), (e) => e.code === 'refusee');
});

test('geolocaliser rejette une précision aberrante', async () => {
  // Au-delà de PRECISION_MAX_M c'est une géolocalisation par IP — souvent le nœud
  // régional du fournisseur d'accès. Un point bleu à 40 km serait pire que rien.
  await assert.rejects(
    () => geolocaliser({
      geo: geoQuiRend({ latitude: 45.7, longitude: 5.2, accuracy: PRECISION_MAX_M + 1 }),
    }),
    (e) => e.code === 'imprecise',
  );
});

test('geolocaliser rejette quand le navigateur n’offre rien', async () => {
  await assert.rejects(() => geolocaliser({ geo: null }), (e) => e.code === 'indisponible');
});

test('geolocaliser accepte une précision inconnue', async () => {
  const p = await geolocaliser({ geo: geoQuiRend({ latitude: 1, longitude: 2 }) });
  assert.equal(p.precision, null);
});

/** Faux stockage : `localStorage` n'existe pas dans Node, et `defineProperty`
 *  passe même si une version future le définit en lecture seule. */
function poserStockage(impl) {
  Object.defineProperty(globalThis, 'localStorage', {
    value: impl, configurable: true, writable: true,
  });
}
const stockageVif = () => {
  const donnees = new Map();
  return {
    donnees,
    getItem: (k) => (donnees.has(k) ? donnees.get(k) : null),
    setItem: (k, v) => donnees.set(k, String(v)),
    removeItem: (k) => donnees.delete(k),
  };
};

test('memoriser puis restaurer rend la commune de la bonne ville', () => {
  poserStockage(stockageVif());
  memoriser('villemoirieu', 'Crémieu');
  assert.equal(restaurer('villemoirieu'), 'Crémieu');
});

test('restaurer ignore la commune d’une autre ville', () => {
  // Une commune du Lévézou n'a aucun sens dans le nord-Isère : mieux vaut ne
  // rien restaurer qu'un point de départ hors du rayon.
  poserStockage(stockageVif());
  memoriser('pont-de-salars', 'Salles-Curan');
  assert.equal(restaurer('villemoirieu'), null);
});

test('oublier efface la mémoire', () => {
  poserStockage(stockageVif());
  memoriser('villemoirieu', 'Crémieu');
  oublier();
  assert.equal(restaurer('villemoirieu'), null);
});

test('restaurer survit à un contenu abîmé', () => {
  const s = stockageVif();
  s.setItem(CLE, 'ceci n’est pas du JSON');
  poserStockage(s);
  assert.equal(restaurer('villemoirieu'), null);
});

test('un stockage interdit ne casse rien', () => {
  // Navigation privée, réglage strict : les trois fonctions doivent se taire.
  poserStockage({
    getItem() { throw new Error('refusé'); },
    setItem() { throw new Error('refusé'); },
    removeItem() { throw new Error('refusé'); },
  });
  assert.doesNotThrow(() => memoriser('villemoirieu', 'Crémieu'));
  assert.doesNotThrow(() => oublier());
  assert.equal(restaurer('villemoirieu'), null);
});
```

- [ ] **Step 3 : Lancer les tests pour vérifier qu'ils échouent**

Run: `cd site && npm test`
Expected: FAIL — `Cannot find module .../src/lib/origine.js`

- [ ] **Step 4 : Écrire `origine.js`**

Créer `site/src/lib/origine.js` :

```js
/** Le point de départ du visiteur — d'où l'on mesure, et ce qu'on ose dessiner.
 *
 * Une position n'est pas un simple {lat, lon} : elle DIT D'OÙ ELLE VIENT. Un
 * point GPS à 15 m près, le centre d'une commune de 2 km et le centre du secteur
 * ne valent pas la même chose, et surtout ne se dessinent pas pareil — le
 * dernier ne se dessine pas du tout. La page retombait jusqu'ici sur le centre
 * du secteur quand la géolocalisation était refusée : acceptable pour estimer un
 * temps de trajet, mensonger sous un marqueur « Vous êtes ici ». Ce module rend
 * la provenance explicite pour que l'affichage puisse en tenir compte.
 *
 * Aucun réseau : les communes sont embarquées dans `sector.json` (géocodage
 * hors-ligne, quelques Ko). Et seul le NOM d'une commune est mémorisé — jamais
 * des coordonnées. Le navigateur mémorise déjà l'autorisation de
 * géolocalisation, donc persister un point GPS exact n'achèterait qu'un clic, au
 * prix d'une donnée sensible écrite sur le disque du visiteur.
 */
import { fold } from './nlsearch.js';

export const CLE = 'quefaire:origin:v1';

/** Au-delà, ce n'est plus du GPS mais une géolocalisation par IP — souvent le
 *  nœud régional du fournisseur d'accès, à des dizaines de kilomètres. On la
 *  traite comme un échec : un faux point bleu est pire qu'un point absent. */
export const PRECISION_MAX_M = 5000;

const echec = (code) => Object.assign(new Error(code), { code });

/**
 * Retrouve une commune du rayon depuis ce que le visiteur a tapé.
 *
 * Correspondance exacte D'ABORD, préfixe ensuite : sur « Saint-Chef », l'ordre
 * du tableau ferait autrement gagner « Saint-Chef-le-Haut ». `fold` neutralise
 * accents, casse, apostrophes et tirets — « ST-CHEF », « saint chef » et
 * « Saint-Chef » désignent la même commune pour qui la tape de mémoire.
 */
export function chercherCommune(communePoints, saisie) {
  const cle = fold((saisie || '').trim());
  if (!cle) return null;
  const liste = communePoints || [];
  return liste.find((c) => fold(c.nom) === cle)
    || liste.find((c) => fold(c.nom).startsWith(cle))
    || null;
}

/**
 * Demande la position au navigateur.
 *
 * `geo` est injectable pour les tests — c'est la seule couture : `navigator`
 * n'existe pas dans Node, et on ne veut pas d'un module qui ne se teste qu'en
 * navigateur. Haute précision demandée : privilégie wifi/GPS à la géoloc par IP.
 */
export function geolocaliser({ geo = globalThis.navigator?.geolocation } = {}) {
  return new Promise((resolve, reject) => {
    if (!geo) return reject(echec('indisponible'));
    geo.getCurrentPosition(
      (pos) => {
        const { latitude, longitude, accuracy } = pos.coords;
        if (accuracy != null && accuracy > PRECISION_MAX_M) return reject(echec('imprecise'));
        resolve({ lat: latitude, lon: longitude, precision: accuracy ?? null });
      },
      () => reject(echec('refusee')),
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
    );
  });
}

/** La mémoire est LIÉE À LA VILLE : la commune retenue pour l'Aveyron n'a aucun
 *  sens dans le nord-Isère, et la restaurer poserait un départ hors du rayon. */
export function memoriser(ville, commune) {
  try {
    localStorage.setItem(CLE, JSON.stringify({ ville, commune }));
  } catch {
    /* stockage interdit ou saturé : le départ reste valable pour cette visite */
  }
}

export function restaurer(ville) {
  try {
    const d = JSON.parse(localStorage.getItem(CLE) || 'null');
    return d && d.ville === ville && d.commune ? d.commune : null;
  } catch {
    // Stockage refusé, ou contenu abîmé : on repart de zéro plutôt que de casser
    // la page. Le point de départ est un agrément, jamais un prérequis.
    return null;
  }
}

export function oublier() {
  try {
    localStorage.removeItem(CLE);
  } catch { /* rien à faire */ }
}
```

- [ ] **Step 5 : Lancer les tests pour vérifier qu'ils passent**

Run: `cd site && npm test`
Expected: PASS — 15 tests, 0 échec.

- [ ] **Step 6 : Vérifier que le site compile toujours**

Run: `cd site && npm run build`
Expected: succès (le module n'est encore importé nulle part, on vérifie juste qu'on n'a rien cassé).

- [ ] **Step 7 : Commit**

```bash
git add site/package.json site/src/lib/origine.js site/tests/origine.test.js
git commit -m "origine : module de résolution du point de départ, et harnais de test

Une position DIT désormais d'où elle vient (geoloc / commune / defaut) : le
repli sur le centre du secteur est acceptable pour estimer un temps de trajet,
mensonger sous un marqueur « Vous êtes ici ».

Lanceur de test intégré à Node — aucune dépendance ajoutée.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `grouperPoints`, extrait et testé

**Files:**
- Create: `site/src/lib/carte.js` (uniquement la fonction pure à ce stade)
- Create: `site/tests/carte.test.js`

**Interfaces:**
- Consumes: rien.
- Produces: `grouperPoints(items, projeter, rayon = 70) → Array<{ lat, lon, pt, items }>` où `items: Array<{lat, lon, ...}>` et `projeter(lat, lon) → { x, y }` (pixels).

**Pourquoi cette tâche existe :** l'algorithme de groupement vit aujourd'hui dans `index.astro:880-902` couplé à `map.project()`, donc intestable. En lui passant la projection en argument, il devient une fonction pure — et c'est le morceau le plus susceptible de se casser en silence quand on touchera au reste.

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `site/tests/carte.test.js` :

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { grouperPoints } from '../src/lib/carte.js';

/** Projection jouet : 1 degré = 1000 px, sans déformation. Suffit pour vérifier
 *  la logique de groupement, qui ne dépend que de distances en pixels. */
const projeter = (lat, lon) => ({ x: lon * 1000, y: lat * 1000 });

test('des points éloignés restent seuls', () => {
  const items = [{ lat: 0, lon: 0 }, { lat: 0, lon: 1 }, { lat: 1, lon: 0 }];
  const groupes = grouperPoints(items, projeter);
  assert.equal(groupes.length, 3);
  assert.ok(groupes.every((g) => g.items.length === 1));
});

test('des points à moins du rayon se regroupent', () => {
  // 0,01° = 10 px, bien sous le rayon de 70 px.
  const items = [{ lat: 0, lon: 0 }, { lat: 0, lon: 0.01 }, { lat: 0.01, lon: 0 }];
  const groupes = grouperPoints(items, projeter);
  assert.equal(groupes.length, 1);
  assert.equal(groupes[0].items.length, 3);
});

test('le groupe se place au centroïde de ses points', () => {
  const items = [{ lat: 0, lon: 0 }, { lat: 0, lon: 0.02 }];
  const [g] = grouperPoints(items, projeter);
  assert.ok(Math.abs(g.lon - 0.01) < 1e-9, `centroïde attendu 0.01, obtenu ${g.lon}`);
  assert.equal(g.lat, 0);
});

test('un point rejoint le groupe le PLUS PROCHE, pas le premier trouvé', () => {
  // Deux groupes dans le rayon : sans le « meilleur », l'ordre du tableau
  // déciderait, et un point pourrait sauter sur un amas plus lointain.
  const items = [{ lat: 0, lon: 0, id: 'a' }, { lat: 0, lon: 0.12, id: 'b' },
                 { lat: 0, lon: 0.115, id: 'c' }];
  const groupes = grouperPoints(items, projeter);
  const avecC = groupes.find((g) => g.items.some((i) => i.id === 'c'));
  assert.ok(avecC.items.some((i) => i.id === 'b'), 'c doit rejoindre b, pas a');
});

test('le rayon est réglable', () => {
  const items = [{ lat: 0, lon: 0 }, { lat: 0, lon: 0.05 }];  // 50 px d'écart
  assert.equal(grouperPoints(items, projeter, 70).length, 1);
  assert.equal(grouperPoints(items, projeter, 20).length, 2);
});

test('une liste vide rend une liste vide', () => {
  assert.deepEqual(grouperPoints([], projeter), []);
});
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `cd site && npm test`
Expected: FAIL — `Cannot find module .../src/lib/carte.js`

- [ ] **Step 3 : Créer `carte.js` avec la seule fonction pure**

Créer `site/src/lib/carte.js` :

```js
/** La carte, et rien d'autre.
 *
 * Ce module ne connaît ni secteur, ni catégorie, ni filtre : il reçoit des
 * points DÉJÀ CUITS — titre, couleur, émoji, infobulle en HTML — et se charge de
 * les placer, de les grouper quand ils se marchent dessus, et de mettre en
 * évidence celui qu'on lui désigne. Tout ce qui relève du vocabulaire du
 * catalogue (libellés de catégorie, horaires, distinctions) reste dans la page,
 * qui est la seule à le connaître.
 *
 * Leaflet est importé DYNAMIQUEMENT : la carte est un mode de vue qu'on active,
 * et la majorité des visites ne l'ouvrent jamais.
 */

/**
 * Regroupe les points qui se marchent dessus à l'écran.
 *
 * `projeter` rend des pixels au zoom courant : c'est ce qui rend la fonction
 * pure, et donc testable. Le groupement se fait en distance ÉCRAN et non en
 * distance terrain — deux musées à 300 m se chevauchent au zoom 9 et pas au
 * zoom 15, et c'est bien le chevauchement qu'on veut résoudre.
 *
 * Un point rejoint le groupe le PLUS PROCHE dans le rayon, jamais le premier
 * rencontré : sinon l'ordre du tableau déciderait, et un point pourrait sauter
 * sur un amas plus lointain que son voisin immédiat.
 */
export function grouperPoints(items, projeter, rayon = 70) {
  const groupes = [];
  for (const it of items) {
    const pt = projeter(it.lat, it.lon);
    let meilleur = null;
    let meilleureD = Infinity;
    for (const g of groupes) {
      const d = Math.hypot(pt.x - g.pt.x, pt.y - g.pt.y);
      if (d < rayon && d < meilleureD) { meilleur = g; meilleureD = d; }
    }
    if (meilleur) {
      meilleur.items.push(it);
      // Centroïde glissant : le groupe se recentre à chaque recrue, sinon il
      // resterait accroché à son premier point et dériverait du nuage réel.
      meilleur.lat += (it.lat - meilleur.lat) / meilleur.items.length;
      meilleur.lon += (it.lon - meilleur.lon) / meilleur.items.length;
      meilleur.pt = projeter(meilleur.lat, meilleur.lon);
    } else {
      groupes.push({ lat: it.lat, lon: it.lon, pt, items: [it] });
    }
  }
  return groupes;
}
```

- [ ] **Step 4 : Lancer les tests pour vérifier qu'ils passent**

Run: `cd site && npm test`
Expected: PASS — 21 tests au total (15 de la tâche 1 + 6 ici).

- [ ] **Step 5 : Commit**

```bash
git add site/src/lib/carte.js site/tests/carte.test.js
git commit -m "carte : groupement des points extrait en fonction pure et testé

Couplé à map.project(), l'algorithme était intestable. La projection passe en
argument : c'est le morceau le plus susceptible de casser en silence quand on
touchera au reste de la carte.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Déménager Leaflet dans `carte.js`

**Files:**
- Modify: `site/src/lib/carte.js` (ajout de `creerCarte`)
- Modify: `site/src/pages/[city]/index.astro:816-1051` (suppression du bloc carte), `:382` (imports), `:586`, `:825-826`, `:1198`

**Interfaces:**
- Consumes: `grouperPoints` (tâche 2).
- Produces: `creerCarte({ el, centre, onSelection }) → Promise<{ afficher(items, opts?), poserOrigine(o), centrerSurOrigine(), survoler(entree), redimensionner() }>`
  - `items[]` : `{ entree, lat, lon, isPlace, unusual, emoji, color, title, href, tooltip }`
  - `opts` : `{ recadrer?: boolean }` (défaut `false`)
  - `o` (origine) : `{ lat, lon, source: 'geoloc'|'commune', libelle, precision }` ou `null`
  - `onSelection(entrees: any[])` : appelé au clic sur un point (`[entree]`), sur un groupe éclaté en liste (toutes ses entrées), ou à la fermeture d'une bulle (`[]`).

**Aucun changement de comportement visible dans cette tâche.** C'est un déménagement : la carte doit se comporter exactement comme avant.

- [ ] **Step 1 : Ajouter `creerCarte` à la fin de `carte.js`**

Après `grouperPoints`, ajouter :

```js
const animationsReduites = () =>
  globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;

/**
 * Crée la carte et rend de quoi la piloter.
 *
 * `onSelection` est le lien carte → liste : la page y branche la mise en
 * évidence de ses tuiles. On lui passe un tableau vide quand plus rien n'est
 * sélectionné (bulle refermée).
 */
export async function creerCarte({ el, centre, onSelection }) {
  const L = (await import('leaflet')).default;
  await import('leaflet/dist/leaflet.css');

  const map = L.map(el, { scrollWheelZoom: false }).setView([centre.lat, centre.lon], 9);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 18,
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);

  const couche = L.layerGroup().addTo(map);
  const coucheOrigine = L.layerGroup().addTo(map);

  let marqueurs = new Map();   // entrée → marqueur (les groupés pointent le leur)
  let derniers = [];           // items du dernier `afficher`, pour redessiner au zoom
  let origine = null;
  let survole = null;
  let enGlissement = false;
  let fermetureTimer = null;

  // Le zoom change la granularité des groupes — sans recadrer la vue.
  map.on('zoomend', () => dessiner(derniers, false));
  map.on('dragstart', () => { enGlissement = true; });
  map.on('dragend', () => { enGlissement = false; });
  // Bulle fermée = plus rien de sélectionné. Le délai laisse le clic sur une
  // AUTRE bulle (qui ferme celle-ci pour ouvrir la sienne) annuler l'effacement.
  map.on('popupclose', () => {
    clearTimeout(fermetureTimer);
    fermetureTimer = setTimeout(() => onSelection([]), 80);
  });

  const choisir = (entrees) => {
    clearTimeout(fermetureTimer);
    onSelection(entrees);
  };

  function ajouterPoint(it) {
    const m = it.isPlace
      // Activité permanente : pastille à icône, nettement distincte du point
      // coloré d'un événement.
      ? L.marker([it.lat, it.lon], {
          icon: L.divIcon({
            className: 'place-icon',
            html: `<div class="place-dot${it.unusual ? ' is-unusual' : ''}">${it.emoji}</div>`,
            iconSize: [30, 30],
            iconAnchor: [15, 15],
          }),
        })
      : L.circleMarker([it.lat, it.lon], {
          radius: 8, color: it.color, fillColor: it.color, fillOpacity: 0.75, weight: 2,
        });
    // Survol : aperçu de la fiche. Clic : on remonte jusqu'à sa tuile dans la
    // liste (mise en évidence + défilement), sans bulle.
    m.bindTooltip(it.tooltip, {
      direction: 'top', offset: [0, -6], opacity: 1, className: 'event-tip',
    })
      .on('click', () => choisir([it.entree]))
      .addTo(couche);
    return m;
  }

  function ouvrirGroupe(groupe) {
    const cadre = L.latLngBounds(groupe.items.map((i) => [i.lat, i.lon]));
    const etendue = cadre.getNorthEast().distanceTo(cadre.getSouthWest());
    if (etendue < 100 || map.getZoom() >= 15) {
      // Tout au même endroit (géocodage à la commune) : liste plutôt que zoom,
      // et mise en évidence de toutes les fiches du groupe dans la grille.
      const liens = groupe.items.slice(0, 12)
        .map((i) => `<a href="${i.href}">${i.title}</a>`).join('<br>');
      const reste = groupe.items.length > 12
        ? `<br>… et ${groupe.items.length - 12} autres` : '';
      L.popup({ maxHeight: 220 })
        .setLatLng([groupe.lat, groupe.lon])
        .setContent(liens + reste)
        .openOn(map);
      choisir(groupe.items.map((i) => i.entree));
    } else {
      map.fitBounds(cadre, { padding: [50, 50] });
    }
  }

  function ajouterGroupe(groupe) {
    const total = groupe.items.length;
    const taille = Math.max(38, Math.min(64, 32 + total * 1.2));
    const quePermanent = groupe.items.every((i) => i.isPlace);
    const quoi = quePermanent ? 'activités' : 'fiches';
    return L.marker([groupe.lat, groupe.lon], {
      icon: L.divIcon({
        className: `cluster-icon${quePermanent ? ' cluster-places' : ''}`,
        html: `<div class="cluster-dot" style="width:${taille}px;height:${taille}px" title="${total} ${quoi} — cliquer pour zoomer">${total}</div>`,
        iconSize: [taille, taille],
        iconAnchor: [taille / 2, taille / 2],
      }),
    }).on('click', () => ouvrirGroupe(groupe)).addTo(couche);
  }

  function dessiner(items, recadrer) {
    couche.clearLayers();
    marqueurs = new Map();
    survole = null;
    const points = [];
    const projeter = (lat, lon) => map.project([lat, lon], map.getZoom());
    for (const groupe of grouperPoints(items, projeter)) {
      if (groupe.items.length >= 2) {
        const m = ajouterGroupe(groupe);
        // Toutes les entrées d'un groupe désignent le marqueur du groupe :
        // survoler une tuile prise dans un amas met l'amas en évidence, faute
        // de pouvoir désigner son point propre.
        for (const it of groupe.items) marqueurs.set(it.entree, m);
        points.push([groupe.lat, groupe.lon]);
      } else {
        const it = groupe.items[0];
        marqueurs.set(it.entree, ajouterPoint(it));
        points.push([it.lat, it.lon]);
      }
    }
    if (!recadrer) return;
    if (!points.length) {
      if (origine) map.setView([origine.lat, origine.lon], 12);
      return;
    }
    const cadre = L.latLngBounds(points);
    // L'origine n'entre dans le cadrage que si elle est DANS le nuage, à une
    // marge près : consulter le nord-Isère depuis Paris ne doit pas donner une
    // carte de France centrée sur le vide. Son marqueur est posé quand même,
    // simplement hors cadre initial.
    if (origine && cadre.pad(0.5).contains([origine.lat, origine.lon])) {
      cadre.extend([origine.lat, origine.lon]);
    }
    map.fitBounds(cadre, { padding: [40, 40], maxZoom: 13 });
  }

  function mettreEnAvant(m, actif) {
    if (!m) return;
    if (m.setStyle) {
      m.setStyle(actif ? { radius: 12, weight: 4 } : { radius: 8, weight: 2 });
    } else {
      m.getElement()?.classList.toggle('is-hover', actif);
    }
    if (actif && m.bringToFront) m.bringToFront();
  }

  return {
    afficher(items, { recadrer = false } = {}) {
      derniers = items;
      dessiner(items, recadrer);
    },

    /** `null` retire le marqueur. Une origine de source `defaut` (centre du
     *  secteur) ne doit JAMAIS arriver ici : la page passe `null` à sa place. */
    poserOrigine(o) {
      origine = o;
      coucheOrigine.clearLayers();
      if (!o) return;
      if (o.source === 'geoloc') {
        // Le cercle de précision dit lui-même ce que le point vaut. En deçà de
        // 50 m il serait plus petit que la pastille : inutile de le dessiner.
        if (o.precision && o.precision > 50) {
          L.circle([o.lat, o.lon], {
            radius: o.precision, color: '#1c7ed6', weight: 1,
            fillColor: '#1c7ed6', fillOpacity: 0.1, className: 'origine-halo',
          }).addTo(coucheOrigine);
        }
        L.circleMarker([o.lat, o.lon], {
          radius: 7, color: '#ffffff', weight: 3,
          fillColor: '#1c7ed6', fillOpacity: 1, className: 'origine-point',
        }).bindPopup('Vous êtes ici').addTo(coucheOrigine);
      } else {
        // Repère VISUELLEMENT DISTINCT du point GPS : 15 m de précision et
        // 2 km de centre-bourg ne se lisent pas pareil sur une carte.
        L.marker([o.lat, o.lon], {
          icon: L.divIcon({
            className: 'origine-icon',
            html: '<div class="origine-maison">🏠</div>',
            iconSize: [30, 30], iconAnchor: [15, 15],
          }),
        }).bindPopup(`Départ : ${o.libelle}<br><small>centre de la commune</small>`)
          .addTo(coucheOrigine);
      }
    },

    centrerSurOrigine() {
      if (!origine) return;
      map.setView([origine.lat, origine.lon], Math.max(map.getZoom(), 12), {
        animate: !animationsReduites(),
      });
    },

    /** Met en évidence le point d'une entrée, et l'amène dans le cadre s'il en
     *  est sorti. `null` relâche la mise en évidence. */
    survoler(entree) {
      const cible = entree ? marqueurs.get(entree) || null : null;
      if (cible === survole) return;
      mettreEnAvant(survole, false);
      survole = cible;
      if (!cible) return;
      mettreEnAvant(cible, true);
      const ou = cible.getLatLng();
      // Pas de recadrage pendant que le visiteur fait glisser la carte : elle
      // lui échapperait des mains.
      if (!enGlissement && !map.getBounds().pad(-0.15).contains(ou)) {
        map.panTo(ou, { animate: !animationsReduites() });
      }
    },

    redimensionner() {
      map.invalidateSize();
    },
  };
}
```

- [ ] **Step 2 : Importer le module dans la page**

Dans `site/src/pages/[city]/index.astro`, sous l'import existant de `nlsearch.js` (ligne 382), ajouter :

```js
  import { creerCarte } from '../../lib/carte.js';
```

- [ ] **Step 3 : Remplacer le bloc carte de la page**

Supprimer intégralement `index.astro:816-1051` — depuis le commentaire `// ---------------- Carte (Leaflet, chargé à la demande) ----------------` jusqu'à la fin du gestionnaire `mapChip.addEventListener(...)` inclus — et le remplacer par le bloc ci-dessous.

Ce bloc **reprend** `selectCards`, `amenerSurSaPage`, `clearMapSelection` et `markerTooltip`, qui restent l'affaire de la page : recopier ce qui suit plutôt que d'essayer de préserver les originaux par un édition chirurgicale. Trois choses ont changé dans ces fonctions reprises — `popupCloseTimer` et son `clearTimeout` disparaissent (le délai vit maintenant dans `carte.js`), et `lastVisible` disparaît aussi. Ce qui **disparaît définitivement** : `initMap`, `buildClusters`, `zoomInto`, `addCluster` et l'ancien `updateMap`.

```js
  // ---------------- Carte (Leaflet, chargé à la demande) ----------------
  // Le pilotage de Leaflet vit dans `lib/carte.js` ; la page ne garde que ce qui
  // demande de connaître le catalogue — le vocabulaire des infobulles et le lien
  // retour vers les tuiles.
  let carte = null;
  let selectedCards = [];

  /** Met en évidence les fiches liées à la bulle cliquée sur la carte — une
   *  seule pour un marqueur isolé, toutes celles d'un groupe qui ne peut pas
   *  s'éclater (fiches au même point). */
  function selectCards(entrees) {
    for (const c of selectedCards) c.classList.remove('map-selected');
    selectedCards = [];
    if (!entrees.length) return;
    // La carte montre tout ce que les filtres retiennent, la liste n'en montre
    // qu'une page : la fiche visée peut donc être ailleurs — et s'il s'agit d'une
    // activité, sa tuile peut ne pas exister encore. On change de page D'ABORD,
    // puis on récupère les éléments, sinon on soulignerait des tuiles absentes.
    amenerSurSaPage(entrees[0]);
    selectedCards = entrees.map(elementDe);
    for (const c of selectedCards) c.classList.add('map-selected');
    selectedCards[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  function amenerSurSaPage(entree) {
    for (const sec of Object.values(SECTIONS)) {
      const i = sec.visible.indexOf(entree);
      if (i < 0) continue;
      const page = Math.floor(i / taillePage()) + 1;
      if (page !== sec.page) {
        sec.page = page;
        rendre(sec);
      }
      return;
    }
  }

  function clearMapSelection() {
    for (const c of selectedCards) c.classList.remove('map-selected');
    selectedCards = [];
  }

  /** Contenu de l'infobulle affichée au survol d'un point : titre, catégorie,
   *  date et commune — comme un aperçu de la tuile. */
  function markerTooltip(it) {
    if (it.isPlace) {
      const cat = (sector.place_categories || {})[it.cat] || 'Activité';
      const lines = [
        `<span class="tt-title">${it.title}</span>`,
        `<span class="tt-line">${it.emoji} ${cat} · <strong>toute l’année</strong></span>`,
      ];
      if (it.unusual) lines.push('<span class="tt-line">✨ Insolite</span>');
      // Pas de note d'avis sur la carte : choix produit, et cela évite au
      // passage la contrainte « pas de contenu Places sur une carte non-Google ».
      if (it.notable) lines.push('<span class="tt-line">🏅 Distinction officielle</span>');
      const opening = openingLabel(it.opening);
      if (opening) lines.push(`<span class="tt-line">🕒 ${opening}</span>`);
      if (it.commune) lines.push(`<span class="tt-line">📍 ${it.commune}</span>`);
      lines.push('<span class="tt-hint">Cliquer pour voir la fiche dans la liste</span>');
      return lines.join('');
    }
    const emoji = CATEGORY_EMOJI[it.cat] || '📌';
    const cat = sector.categories[it.cat] || '';
    const when = formatWhen(it.date, it.end);
    const lines = [
      `<span class="tt-title">${it.title}</span>`,
      `<span class="tt-line">${emoji} ${cat}${when ? ` · ${when}` : ''}</span>`,
    ];
    if (it.commune) lines.push(`<span class="tt-line">📍 ${it.commune}</span>`);
    lines.push('<span class="tt-hint">Cliquer pour voir la fiche dans la liste</span>');
    return lines.join('');
  }

  /** Traduit les entrées retenues par le filtre en points de carte. Les points
   *  se construisent depuis les DONNÉES de l'entrée, jamais depuis son élément :
   *  une activité peut n'avoir aucune tuile fabriquée (elle est sur une autre
   *  page), et la carte doit malgré tout la montrer — elle suit le filtre, pas
   *  la pagination. */
  function itemsDe(visible) {
    const racine = getComputedStyle(document.documentElement);
    const items = [];
    for (const v of visible) {
      const { data, isPlace, notable } = v;
      if (Number.isNaN(data.lat)) continue;
      const it = {
        entree: v,
        lat: data.lat, lon: data.lon, cat: data.cat, commune: data.commune,
        date: data.date, end: data.end,
        isPlace, notable,
        unusual: data.unusual === 'true',
        emoji: isPlace ? PLACE_EMOJI[data.cat] || '📍' : CATEGORY_EMOJI[data.cat] || '📌',
        opening: v.opening || '',
        title: (v.nom || '').replace(/\s*↗$/, ''),
        href: v.href,
        color: racine.getPropertyValue(`--${isPlace ? 'p' : 'c'}-${data.cat}`).trim() || 'currentColor',
      };
      it.tooltip = markerTooltip(it);
      items.push(it);
    }
    return items;
  }

  function updateMap(visible, recadrer) {
    if (!carte || mapWrap.hidden) return;
    carte.afficher(itemsDe(visible), { recadrer });
  }

  mapChip.addEventListener('click', async () => {
    mapWrap.hidden = !mapWrap.hidden;
    mapChip.setAttribute('aria-pressed', String(!mapWrap.hidden));
    if (!mapWrap.hidden) {
      if (!carte) {
        carte = await creerCarte({
          el: document.getElementById('map'),
          centre: sector.center,
          onSelection: selectCards,
        });
      }
      carte.redimensionner();
      apply();
    }
  });
```

- [ ] **Step 4 : Nettoyer les trois références au Leaflet disparu**

1. `index.astro:586` — dans `apply()`, la ligne `clearMapSelection();` reste inchangée (la fonction existe toujours).
2. `index.astro:691-692` — remplacer les deux lignes par la seule qui reste :

```js
    updateMap(visible, true);
```

(`lastVisible` disparaît : `carte.js` mémorise lui-même les derniers points pour redessiner au zoom.)

3. `index.astro:1198` — dans `ouvrirReglages`, remplacer :

```js
    if (carte && !mapWrap.hidden) requestAnimationFrame(() => carte.redimensionner());
```

- [ ] **Step 5 : Vérifier que la compilation passe**

Run: `cd site && npm run build && npm test`
Expected: build réussi, 21 tests au vert.

- [ ] **Step 6 : Vérifier à l'œil que la carte n'a pas changé**

Run: `cd site && npm run dev`, ouvrir `/villemoirieu/`, cliquer 🗺️ Carte.
Expected, à l'identique d'avant :
- les points apparaissent et le cadrage englobe le secteur ;
- survoler un point montre l'infobulle (titre, catégorie, date, commune) ;
- cliquer un point surligne la bonne tuile et y fait défiler ;
- cliquer une pastille numérotée zoome, ou ouvre une liste de liens si tout est au même endroit ;
- zoomer regroupe et dégroupe les points ;
- ouvrir/fermer la colonne de réglages ne laisse pas de tuiles grises.

- [ ] **Step 7 : Commit**

```bash
git add site/src/lib/carte.js "site/src/pages/[city]/index.astro"
git commit -m "carte : Leaflet déménage dans lib/carte.js

index.astro faisait 1396 lignes dont 230 de Leaflet ; y ajouter le bandeau
collant, le bouton de position et le survol l'aurait rendu impraticable.

Le module reçoit des points DÉJÀ CUITS et ne connaît ni secteur, ni catégorie,
ni filtre : le vocabulaire du catalogue reste dans la page. Aucun changement de
comportement.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Le bandeau collant

**Files:**
- Modify: `site/src/styles/global.css:21` (variables), `:411-418` (bloc carte), `:470-473` (grille)
- Modify: `site/src/pages/[city]/index.astro:391-402` (mesures), gestionnaire `mapChip`

**Interfaces:**
- Consumes: `carte.redimensionner()` (tâche 3).
- Produces: les variables CSS `--top-h` (hauteur de `.explore-top`) et `--sticky-h` (hauteur totale collée : en-tête + barre + carte si affichée), mises à jour par `majHauteursCollantes()`.

- [ ] **Step 1 : Remplacer la mesure de l'en-tête par une mesure de tout ce qui colle**

Dans `index.astro`, remplacer le bloc `:391-402` (de `// La hauteur de l'en-tête collant est MESURÉE` jusqu'à `new ResizeObserver(majHauteurEntete).observe(entete);` inclus) par :

```js
  // Les hauteurs de ce qui COLLE sont MESURÉES, pas supposées. Elles décalent
  // tous les `position: sticky` de la page, et elles changent avec la largeur —
  // sur téléphone le menu passe à la ligne et l'en-tête triple de hauteur. La
  // valeur de 57 px codée en CSS cachait alors la barre de recherche DERRIÈRE
  // l'en-tête : le réglage censé rester sous la main était le seul invisible.
  //
  // `--sticky-h` est la somme : c'est ce que recouvre le haut de l'écran, donc
  // la marge de défilement dont une tuile ou un titre de section a besoin pour
  // ne pas arriver CACHÉ sous la carte.
  const entete = document.querySelector('.site-header');
  const barreHaute = document.querySelector('.explore-top');
  const racineCss = document.documentElement;

  function majHauteursCollantes() {
    const h = entete ? Math.round(entete.getBoundingClientRect().height) : 0;
    const t = barreHaute ? Math.round(barreHaute.getBoundingClientRect().height) : 0;
    const c = mapWrap.hidden ? 0 : Math.round(mapWrap.getBoundingClientRect().height);
    racineCss.style.setProperty('--header-h', `${h}px`);
    racineCss.style.setProperty('--top-h', `${t}px`);
    racineCss.style.setProperty('--sticky-h', `${h + t + c}px`);
  }

  const observateurCollant = new ResizeObserver(majHauteursCollantes);
  for (const el of [entete, barreHaute, mapWrap]) {
    if (el) observateurCollant.observe(el);
  }
  majHauteursCollantes();
```

**Attention à l'ordre :** ce bloc référence `mapWrap`, qui est déclaré plus bas (`index.astro:458`). Déplacer les deux lignes suivantes **avant** ce bloc, en les retirant de leur emplacement actuel :

```js
  const mapChip = document.getElementById('mapchip');
  const mapWrap = document.getElementById('mapwrap');
```

- [ ] **Step 2 : Recalculer à chaque bascule de la carte**

Dans le gestionnaire `mapChip.addEventListener` écrit à la tâche 3, ajouter l'appel juste après le changement de `hidden` — un `ResizeObserver` ne se déclenche pas de façon fiable quand un élément passe en `display: none` :

```js
  mapChip.addEventListener('click', async () => {
    mapWrap.hidden = !mapWrap.hidden;
    mapChip.setAttribute('aria-pressed', String(!mapWrap.hidden));
    // Fermer la carte doit RENDRE au défilement les 300 px qu'elle réservait :
    // sans ce rappel, `--sticky-h` garderait la hauteur d'une carte absente et
    // toutes les cibles de défilement s'arrêteraient trop tôt.
    majHauteursCollantes();
    if (!mapWrap.hidden) {
      if (!carte) {
        carte = await creerCarte({
          el: document.getElementById('map'),
          centre: sector.center,
          onSelection: selectCards,
        });
      }
      carte.redimensionner();
      apply();
    }
  });
```

- [ ] **Step 3 : Rendre le bandeau collant en CSS**

Dans `global.css`, ajouter les trois variables à `:root`, juste après `--header-h` (ligne 21) :

```css
  --top-h: 64px;      /* barre de recherche collante — mesurée par le script */
  --sticky-h: 121px;  /* en-tête + barre + carte : ce qui recouvre le haut */
  --map-h: clamp(180px, 34vh, 300px);
```

Puis remplacer le bloc `:413-418` par :

```css
/* La carte est un BANDEAU COLLANT, pas un bloc qu'on dépasse. Tout l'intérêt
   est de situer une activité PENDANT qu'on lit sa tuile : une carte qu'on perd
   de vue au premier défilement ne sert qu'à l'instant où on l'ouvre.

   Elle colle sous la barre de recherche, qui colle elle-même sous l'en-tête —
   d'où le `top` composé, avec des hauteurs mesurées et non devinées. */
#mapwrap {
  position: sticky;
  top: calc(var(--header-h) + var(--top-h));
  z-index: 14;            /* sous la barre de recherche (15), sur les tuiles */
  margin-bottom: 20px;
  padding-bottom: 8px;
  background: var(--bg);
  /* Même artifice que `.explore-top` : sans ces ombres latérales, les tuiles se
     voient en transparence sur les bords pendant le défilement, la colonne
     n'ayant pas de marge intérieure. */
  box-shadow: -14px 0 0 var(--bg), 14px 0 0 var(--bg);
}
#map {
  height: var(--map-h);
  border-radius: var(--radius); border: 1px solid var(--line);
  box-shadow: var(--shadow); z-index: 1;
}
.map-note { font-size: 0.78rem; color: var(--ink-soft); margin: 6px 2px 0; }

/* Un bandeau permanent ne peut pas prendre la moitié d'un petit écran. */
@media (max-width: 720px) { :root { --map-h: clamp(150px, 28vh, 220px); } }
@media (max-height: 560px) { :root { --map-h: 150px; } }
```

- [ ] **Step 4 : Donner aux cibles de défilement la marge qui leur manque**

Dans `global.css`, juste après le bloc `.grid` (`:470-473`), ajouter :

```css
/* Sans cette marge, « aller à la page 3 » ou un clic sur un point de la carte
   feraient défiler leur cible DERRIÈRE la carte collante : le visiteur verrait
   une tuile surlignée qu'il n'a pas sous les yeux. */
.section, .card { scroll-margin-top: calc(var(--sticky-h) + 16px); }
```

- [ ] **Step 5 : Vérifier**

Run: `cd site && npm run build && npm run dev`
Sur `/villemoirieu/`, carte ouverte :
- descendre jusqu'aux dernières tuiles : la carte **reste visible**, sous la barre de recherche, sans la recouvrir ;
- aucune tuile ne se voit en transparence sur les bords ou sous la note de bas de carte ;
- cliquer une pastille de page (2, 3…) amène le titre de section **sous** la carte, pas derrière ;
- fermer la carte avec 🗺️ : le défilement retrouve sa marge normale, aucun espace mort en haut ;
- réduire la fenêtre à 390 px : la carte ne dépasse pas ~28 % de la hauteur.

- [ ] **Step 6 : Commit**

```bash
git add site/src/styles/global.css "site/src/pages/[city]/index.astro"
git commit -m "carte : bandeau collant sous la barre de recherche

La carte disparaissait au premier défilement — elle ne servait qu'à l'instant
où on l'ouvrait. Elle colle désormais sous la barre de recherche et les tuiles
défilent dessous.

Hauteurs mesurées (--top-h, --sticky-h) et non devinées : sur téléphone
l'en-tête triple de hauteur, et une constante cacherait les cibles de
défilement derrière la carte.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Origine typée, marqueurs honnêtes et bouton 📍

**Files:**
- Modify: `site/src/pages/[city]/index.astro:324-327` (balisage), `:382` (imports), `:460` (état), `:518-573` (point de départ), `:1136-1151` (`resetAll`), `:1245-1251` (pastille)
- Modify: `site/src/styles/global.css` (bouton et marqueurs d'origine)

**Interfaces:**
- Consumes: `chercherCommune`, `geolocaliser`, `memoriser`, `oublier` (tâche 1) ; `carte.poserOrigine`, `carte.centrerSurOrigine` (tâche 3).
- Produces:
  - `origine` : `{ lat, lon, source: 'geoloc'|'commune'|'defaut', libelle, precision } | null` — remplace `userPos` partout.
  - `appliquerOrigine(o, { message } = {}) → void`
  - `assurerOrigine() → Promise<origine>` — remplace `requestGeoloc(cb)`
  - `setOrigin(saisie) → boolean` (signature inchangée, corps réécrit)

- [ ] **Step 1 : Ajouter le bouton et la zone de message au balisage**

Remplacer `index.astro:324-327` par :

```astro
      <div id="mapwrap" hidden>
        {/* Le cadre porte le positionnement : `#map` crée son propre contexte
            d'empilement (z-index: 1), donc un bouton posé DEDANS passerait sous
            les couches de Leaflet. */}
        <div class="map-frame">
          <div id="map"></div>
          {/* Un bouton HTML plutôt qu'un contrôle Leaflet : il doit se styler
              avec le reste de la page et rester dans l'ordre de tabulation du
              document. */}
          <button type="button" class="map-locate" id="map-locate" aria-pressed="false">
            <span class="ml-icon" aria-hidden="true">📍</span>
            <span class="ml-label">Ma position</span>
          </button>
        </div>
        <p class="map-note">Temps de trajet estimés à vol d'oiseau corrigé — un calcul d'itinéraire précis arrive en roadmap.</p>
        {/* Le même message qu'en colonne de réglages, répété ici : la colonne
            peut être fermée, et un échec de localisation doit se lire à l'endroit
            où on vient de cliquer. */}
        <p class="map-geo" id="map-geo" role="status"></p>
      </div>
```

- [ ] **Step 2 : Importer le module `origine.js`**

Sous l'import de `carte.js` ajouté à la tâche 3 :

```js
  import { chercherCommune, geolocaliser, memoriser, oublier as oublierOrigine, restaurer } from '../../lib/origine.js';
```

`oublier` est renommé à l'import : `prefs.js` exporte déjà une fonction de ce nom, importée ligne 385.

- [ ] **Step 3 : Remplacer `userPos` par `origine`**

`index.astro:460` — remplacer :

```js
  let origine = null;  // { lat, lon, source: 'geoloc'|'commune'|'defaut', libelle, precision }
```

Et ajouter, avec les autres constantes d'éléments :

```js
  const locateBtn = document.getElementById('map-locate');
  const mapGeo = document.getElementById('map-geo');
```

- [ ] **Step 4 : Réécrire le bloc « Point de départ »**

Remplacer `index.astro:518-573` (du commentaire `// ---------------- Point de départ ----------------` jusqu'à la fin de `requestGeoloc`) par :

```js
  // ---------------- Point de départ ----------------
  // Refuser la géolocalisation ne doit pas priver du filtre temps de trajet :
  // on retombait sur le centre du secteur, ce qui donne des temps faux pour qui
  // habite le bord du rayon. Le visiteur peut donc NOMMER sa commune. La table
  // de coordonnées est embarquée dans sector.json (géocodage hors-ligne, quelques
  // Ko) : aucun appel réseau, aucune donnée envoyée nulle part — et la précision
  // au centre de la commune est exactement celle de nos temps de trajet.
  //
  // Une origine DIT D'OÙ ELLE VIENT, et le marqueur en dépend : `defaut` (centre
  // du secteur) sert à calculer, jamais à afficher. Un point « Vous êtes ici »
  // posé sur une valeur de repli serait un mensonge.
  const originEl = document.getElementById('origin');
  const communePoints = sector.commune_points || [];

  function messageOrigine(texte) {
    geostate.textContent = texte;
    mapGeo.textContent = texte;
  }

  function appliquerOrigine(o, { message = '' } = {}) {
    origine = o;
    if (carte) carte.poserOrigine(o && o.source !== 'defaut' ? o : null);
    majBoutonPosition();
    messageOrigine(message);
  }

  function majBoutonPosition(occupe = false) {
    if (!locateBtn) return;
    const src = origine?.source;
    locateBtn.disabled = occupe;
    locateBtn.setAttribute('aria-busy', String(occupe));
    locateBtn.setAttribute('aria-pressed', String(src === 'geoloc'));
    locateBtn.classList.toggle('is-on', src === 'geoloc' || src === 'commune');
    locateBtn.querySelector('.ml-label').textContent = occupe ? 'Localisation…' : 'Ma position';
    locateBtn.title = occupe ? ''
      : src === 'geoloc' ? 'Recentrer la carte sur votre position'
        : src === 'commune' ? `Utiliser votre position réelle plutôt que ${origine.libelle}`
          : 'Afficher votre position sur la carte';
  }

  /** Applique la commune saisie comme point de départ. Rend true si reconnue. */
  function setOrigin(saisie) {
    const brut = (saisie || '').trim();
    if (!brut) {
      appliquerOrigine(null);
      oublierOrigine();
      return true;
    }
    const trouve = chercherCommune(communePoints, brut);
    if (!trouve) {
      messageOrigine(`« ${brut} » n'est pas dans le rayon — essayez une commune voisine.`);
      return false;
    }
    if (originEl) originEl.value = trouve.nom;
    memoriser(sector.id, trouve.nom);
    appliquerOrigine(
      { lat: trouve.lat, lon: trouve.lon, source: 'commune', libelle: trouve.nom, precision: null },
      { message: `Départ : ${trouve.nom}` },
    );
    return true;
  }

  const MESSAGES_GEO = {
    indisponible: 'Géolocalisation non disponible — indiquez votre commune ci-dessus.',
    imprecise: 'Position trop imprécise (localisation par le réseau) — indiquez votre commune ci-dessus.',
    refusee: 'Position refusée — indiquez votre commune ci-dessus.',
  };

  /** Garantit une origine pour les FILTRES qui en exigent une. À la différence du
   *  bouton 📍, cette voie se rabat sur le centre du secteur en cas d'échec :
   *  mieux vaut un temps de trajet approximatif et annoncé comme tel que pas de
   *  filtre du tout. Le marqueur, lui, n'est pas dessiné — `defaut` n'est pas
   *  une position. */
  async function assurerOrigine() {
    if (origine) return origine;
    try {
      const p = await geolocaliser();
      appliquerOrigine({ ...p, source: 'geoloc', libelle: 'Vous êtes ici' });
    } catch (err) {
      appliquerOrigine(
        { lat: sector.center.lat, lon: sector.center.lon, source: 'defaut', libelle: sector.name, precision: null },
        { message: `${MESSAGES_GEO[err.code] || MESSAGES_GEO.refusee} Calcul depuis ${sector.name} en attendant.` },
      );
      if (originEl) originEl.focus();
    }
    return origine;
  }
```

- [ ] **Step 5 : Brancher le bouton**

Juste après le gestionnaire de `mapChip` (tâches 3 et 4), ajouter :

```js
  // Un geste EXPLICITE, jamais une demande automatique à l'ouverture de la
  // carte : une boîte de dialogue du navigateur que personne n'a réclamée est le
  // contraire de la ligne du projet — rien ne se déclenche dans le dos du
  // visiteur, et rien ne quitte son navigateur.
  locateBtn.addEventListener('click', async () => {
    if (origine?.source === 'geoloc') {
      carte.centrerSurOrigine();
      return;
    }
    majBoutonPosition(true);
    try {
      const p = await geolocaliser();
      // La position précise remplace la commune : le champ se vide, sa pastille
      // disparaît, et la mémoire suit. La règle est que LA MÉMOIRE EST LE REFLET
      // DU CHAMP — une seule règle, donc rien à deviner.
      if (originEl) originEl.value = '';
      oublierOrigine();
      appliquerOrigine({ ...p, source: 'geoloc', libelle: 'Vous êtes ici' });
      carte.centrerSurOrigine();
      apply();
    } catch (err) {
      // Un échec ne fait JAMAIS perdre une commune déjà posée : `origine` reste
      // en place, seul le message change.
      majBoutonPosition(false);
      messageOrigine(`${MESSAGES_GEO[err.code] || MESSAGES_GEO.refusee} (☰ Réglages)`);
      ouvrirReglages(true, { rendreLeFocus: false });
      if (originEl) originEl.focus();
    }
  });
```

- [ ] **Step 6 : Poser l'origine à l'ouverture de la carte**

Dans le gestionnaire `mapChip`, juste après `carte.redimensionner();` :

```js
      carte.poserOrigine(origine && origine.source !== 'defaut' ? origine : null);
```

Sans cela, une commune saisie avant d'ouvrir la carte n'aurait pas de marqueur : `appliquerOrigine` ne peut rien poser tant que `carte` est `null`.

- [ ] **Step 7 : Remplacer les derniers usages de `userPos`**

1. `apply()` (`index.astro:593-596`) — remplacer :

```js
    if (needsGeo && !origine) {
      assurerOrigine().then(apply);
      return;
    }
```

2. `apply()` (`:609` et `:618`) — remplacer `userPos` par `origine` dans les deux occurrences :

```js
      if (ok && needsGeo && origine) {
```
```js
          dist = distanceKm(origine.lat, origine.lon, data.lat, data.lon);
```

3. `resetAll()` (`:1147-1148`) — remplacer les deux lignes par :

```js
    appliquerOrigine(null);
    oublierOrigine();
```

4. Pastille du point de départ (`:1245-1251`) — remplacer le corps du retrait :

```js
    if (originEl && originEl.value.trim()) {
      pastilles.push([`🏠 ${originEl.value.trim()}`, () => {
        originEl.value = '';
        appliquerOrigine(null);
        oublierOrigine();
      }]);
    }
```

- [ ] **Step 8 : Habiller le bouton et les marqueurs**

Dans `global.css`, après le bloc `#mapwrap` de la tâche 4 :

```css
.map-frame { position: relative; }

/* Au-dessus de la carte : `#map` a son propre contexte d'empilement (z-index: 1)
   et contient les couches de Leaflet, donc un simple 2 suffit ici. */
.map-locate {
  position: absolute; top: 10px; right: 10px; z-index: 2;
  display: flex; align-items: center; gap: 6px;
  font: inherit; font-size: 0.84rem; font-weight: 700;
  padding: 7px 12px; border-radius: 999px; cursor: pointer;
  background: var(--card); color: var(--ink);
  border: 1.5px solid var(--line); box-shadow: var(--shadow-lift);
}
.map-locate:hover { border-color: var(--accent); }
.map-locate.is-on { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
.map-locate[disabled] { opacity: 0.7; cursor: progress; }
.map-geo { font-size: 0.8rem; color: var(--accent); font-weight: 600; margin: 6px 2px 0; min-height: 1.1em; }

/* Deux repères VISUELLEMENT DISTINCTS : 15 m de GPS et 2 km de centre-bourg ne
   se lisent pas pareil sur une carte, et les confondre tromperait sur ce que
   valent les temps de trajet affichés. */
.origine-icon { background: none; border: none; }
.origine-maison {
  display: flex; align-items: center; justify-content: center;
  width: 30px; height: 30px; border-radius: 50%; font-size: 1rem;
  background: var(--card); border: 2.5px solid var(--accent);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
}
.origine-point { filter: drop-shadow(0 1px 3px rgba(0, 0, 0, 0.4)); }

/* Sous 720 px le libellé tombe : à 390 px, « 📍 Ma position » et les commandes
   de zoom se disputaient la largeur de la carte. */
@media (max-width: 720px) {
  .map-locate .ml-label { display: none; }
  .map-locate { padding: 8px 10px; }
}
```

- [ ] **Step 9 : Vérifier**

Run: `cd site && npm run build && npm test && npm run dev`

Sur `/villemoirieu/`, carte ouverte :
1. Cliquer 📍 et **refuser** dans le navigateur : **aucun marqueur** n'apparaît sur la carte, le message renvoie vers ☰ Réglages, la colonne s'ouvre et le champ « Point de départ » prend le focus.
2. Taper « Crémieu » dans le champ : épingle 🏠 sur la carte, message « Départ : Crémieu », bouton 📍 en état actif avec l'infobulle « Utiliser votre position réelle plutôt que Crémieu ».
3. Cliquer 📍 et **accepter** : point bleu cerclé de blanc, cercle de précision si elle dépasse 50 m, champ « Point de départ » vidé, carte centrée dessus.
4. Recliquer 📍 : la carte se recentre, aucune nouvelle demande de permission.
5. « Tout réafficher » : le marqueur disparaît.
6. Poser « Près de moi » sans avoir donné de position, puis refuser : les temps s'affichent depuis le centre du secteur, le message le dit, et **toujours aucun marqueur**.

- [ ] **Step 10 : Commit**

```bash
git add site/src/styles/global.css "site/src/pages/[city]/index.astro"
git commit -m "carte : bouton de position, et un marqueur qui ne ment pas

Le point « Vous êtes ici » n'apparaissait que comme effet de bord d'un filtre
qui exige la distance. Un bouton 📍 le demande explicitement.

L'origine porte désormais sa provenance : le repli sur le centre du secteur
(source « defaut ») calcule des temps de trajet mais ne dessine aucun marqueur,
et une position GPS et un centre de commune ont deux repères distincts.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Temps de trajet dès qu'une origine existe

**Files:**
- Modify: `site/src/pages/[city]/index.astro:585-632` (`apply` / `juger`)

**Interfaces:**
- Consumes: `origine`, `assurerOrigine` (tâche 5).
- Produits : aucun nouveau symbole. `needsGeo` change de rôle — il ne décide plus que de la RÉCLAMATION d'une position.

- [ ] **Step 1 : Séparer « réclamer une position » de « mesurer »**

Dans `apply()`, remplacer le bloc `:588-596` par :

```js
    // Trier par distance exige un point de départ, exactement comme « Près de
    // moi » : choisir ce tri déclenche donc la même demande (géolocalisation, ou
    // invitation à nommer sa commune si elle est refusée).
    const needsGeo = filter.nearMe || filter.maxMinutes != null || sortEl.value === 'distance';

    if (needsGeo && !origine) {
      assurerOrigine().then(apply);
      return;
    }

    // On MESURE dès qu'on sait d'où : cliquer 📍 doit suffire à faire apparaître
    // « ≈ 12 min en voiture » sur les tuiles, sans obliger à poser un filtre dont
    // on n'a que faire. `needsGeo` ne décide plus que de RÉCLAMER une position.
    const mesurer = origine != null;
```

- [ ] **Step 2 : Mesurer sur cette condition**

Dans `juger`, remplacer la ligne `:609` :

```js
      if (ok && mesurer) {
```

Le reste du bloc est **inchangé** — en particulier `if (filter.maxMinutes != null && minutes > filter.maxMinutes) ok = false;` et l'exclusion des fiches sans coordonnées, qui restent conditionnées au filtre. Poser une position ne doit jamais masquer une fiche.

- [ ] **Step 3 : Vérifier**

Run: `cd site && npm run build && npm run dev`

Sur `/villemoirieu/` :
1. Sans position : aucune tuile ne porte de temps de trajet.
2. Taper « Crémieu » : **toutes** les tuiles affichent « ≈ N min en voiture », **aucun** chip n'est activé, le compteur ne bouge pas — rien n'a été filtré.
3. Changer « 🚗 Moyen » pour « à pied » : les temps se recalculent.
4. Poser « ⏱ Trajet max. 15 min » : là seulement, la liste se raccourcit, et le compteur mentionne les fiches sans lieu précis écartées.
5. « Tout réafficher » : les temps disparaissent avec l'origine.

- [ ] **Step 4 : Commit**

```bash
git add "site/src/pages/[city]/index.astro"
git commit -m "distances : mesurer dès qu'une origine existe, filtre ou pas

Le temps de trajet était conditionné à un filtre géographique : donner sa
position ne suffisait pas à savoir à quelle distance étaient les activités,
alors que c'est exactement la question posée.

needsGeo ne décide plus que de RÉCLAMER une position. Le filtrage par
maxMinutes reste, lui, strictement conditionné au filtre : une position ne doit
jamais masquer une fiche.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Survoler une tuile met son point en évidence

**Files:**
- Modify: `site/src/pages/[city]/index.astro:736-751` (`elementDe`), + nouveau bloc d'écoute
- Modify: `site/src/styles/global.css` (états `.is-hover`)

**Interfaces:**
- Consumes: `carte.survoler(entree)` (tâche 3), `SECTIONS` (existant), `elementDe` (existant).
- Produces: `entreeDe: WeakMap<Element, entree>` et `survolerDepuis(el | null) → void` (anti-rebond de 80 ms).

- [ ] **Step 1 : Mémoriser l'entrée que porte chaque tuile**

Dans `elementDe` (`:738-751`), ajouter l'enregistrement juste après la récupération de la carte. C'est le seul endroit par lequel passent les deux sortes de tuiles — événements pré-rendus et activités fabriquées :

```js
  /** Élément d'une entrée : la carte pré-rendue d'un événement, ou la tuile
   *  d'activité fabriquée à la demande. */
  function elementDe(v) {
    const card = v.card ?? tuileDe(v.index);
    // Le chemin RETOUR, tuile → entrée : c'est ce qui permet au survol d'une
    // tuile de désigner son point sur la carte. Passage obligé des deux sortes
    // de tuiles, donc le seul endroit où l'inscrire.
    entreeDe.set(card, v);
    // Le temps de trajet s'écrit ici : pour une activité, la tuile peut n'exister
    // qu'au moment de son affichage, donc après le calcul de la distance.
    const distEl = card.querySelector('[data-role="distance"]');
```

(le reste de la fonction est inchangé)

- [ ] **Step 2 : Déclarer la table et brancher les écoutes**

Après la définition de `updateMap` (tâche 3), ajouter :

```js
  // ---------------- Survol d'une tuile → son point sur la carte ------------
  // Le lien carte → liste existait déjà (cliquer un point remonte à sa tuile).
  // C'est le chemin INVERSE qui manquait, et c'est le plus fréquent : on parcourt
  // la liste, et on veut savoir où se trouve ce qu'on lit.
  const entreeDe = new WeakMap();
  let survolTimer = null;

  function survolerDepuis(el) {
    clearTimeout(survolTimer);
    // Anti-rebond : sans lui, un passage de souris sur une rangée déclenche six
    // recadrages, et la carte part en tous sens.
    survolTimer = setTimeout(() => {
      if (!carte || mapWrap.hidden) return;
      carte.survoler(el ? entreeDe.get(el) || null : null);
    }, 80);
  }

  for (const sec of Object.values(SECTIONS)) {
    // `pointer*` plutôt que `mouse*` : le survol n'existe pas au doigt, et une
    // tape navigue déjà vers la fiche — mettre un point en évidence au passage
    // ferait bouger la carte sous un doigt qui essaie de faire défiler.
    sec.grid.addEventListener('pointerover', (e) => {
      if (e.pointerType === 'touch') return;
      survolerDepuis(e.target.closest('.card'));
    });
    sec.grid.addEventListener('pointerout', (e) => {
      if (e.pointerType === 'touch') return;
      if (!e.relatedTarget || !sec.grid.contains(e.relatedTarget)) survolerDepuis(null);
    });
    // Le clavier a droit au même repère que la souris : les tuiles sont des
    // liens, donc elles reçoivent le focus à la tabulation.
    sec.grid.addEventListener('focusin', (e) => survolerDepuis(e.target.closest('.card')));
    sec.grid.addEventListener('focusout', (e) => {
      if (!e.relatedTarget || !sec.grid.contains(e.relatedTarget)) survolerDepuis(null);
    });
  }
```

- [ ] **Step 3 : Habiller la mise en évidence**

Dans `global.css`, après les styles de `.cluster-dot` (`:419-428`) :

```css
/* Point mis en évidence parce qu'on survole sa tuile. Les points d'événement
   sont des `circleMarker` — c'est Leaflet qui les grossit, pas le CSS. */
.place-icon.is-hover .place-dot,
.cluster-icon.is-hover .cluster-dot {
  transform: scale(1.22);
  box-shadow: 0 3px 12px rgba(0, 0, 0, 0.4);
}
.place-icon .place-dot, .cluster-icon .cluster-dot { transition: transform 0.12s; }

@media (prefers-reduced-motion: reduce) {
  .place-icon .place-dot, .cluster-icon .cluster-dot { transition: none; }
}
```

- [ ] **Step 4 : Vérifier**

Run: `cd site && npm run build && npm run dev`

Sur `/villemoirieu/`, carte ouverte :
1. Survoler une tuile : son point grossit sur la carte.
2. Survoler une tuile dont le point est hors du cadre : la carte s'y déplace.
3. Balayer rapidement une rangée : la carte ne part pas en tous sens (anti-rebond).
4. Quitter la grille : la mise en évidence se relâche.
5. Tabuler dans la grille au clavier : même comportement.
6. Faire glisser la carte en la survolant d'une main et une tuile de l'autre — plus simplement : commencer un glissement de carte ; le survol ne doit pas la recadrer sous les doigts.
7. Sur téléphone (mode responsive du navigateur, émulation tactile) : faire défiler la liste ne fait pas bouger la carte.
8. Survoler une tuile prise dans une pastille numérotée : c'est la pastille qui se met en évidence.

- [ ] **Step 5 : Commit**

```bash
git add site/src/styles/global.css "site/src/pages/[city]/index.astro"
git commit -m "carte : survoler une tuile met son point en évidence

Le lien carte → liste existait ; c'est le chemin inverse qui manquait, et c'est
le plus fréquent — on parcourt la liste, et on veut savoir où est ce qu'on lit.

Anti-rebond de 80 ms, pas de recadrage pendant un glissement, et rien au doigt :
faire défiler une liste ne doit pas faire bouger la carte.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Mémoire de la commune

**Files:**
- Modify: `site/src/pages/[city]/index.astro:1388-1395` (amorçage)

**Interfaces:**
- Consumes: `restaurer` (tâche 1), `setOrigin` (tâche 5).
- Produces: aucun nouveau symbole.

`memoriser` et `oublierOrigine` sont déjà appelés par `setOrigin`, le bouton 📍, `resetAll` et le retrait de la pastille (tâche 5). Il ne reste que la restauration au chargement.

- [ ] **Step 1 : Restaurer avant le premier calcul**

Remplacer la fin du script (`:1388-1395`) par :

```js
  if (nbVotes(prefs)) activerTriGouts(true);
  majTuner();

  // Point de départ mémorisé — le NOM d'une commune, jamais des coordonnées. Il
  // pose l'origine et fait apparaître les temps de trajet, mais AUCUN FILTRE :
  // on restaure d'où l'on part, pas une recherche. Restaurer un filtre à
  // l'ouverture donnerait une liste amputée sans que rien ne l'explique.
  const communeMemorisee = originEl ? restaurer(sector.id) : null;
  if (communeMemorisee) setOrigin(communeMemorisee);

  // Premier calcul au chargement. Sans lui, le compteur gardait la valeur rendue
  // par le serveur — « 36 événements » — alors que la grille affichait déjà les
  // 336 tuiles, événements ET activités permanentes : le chiffre annonçait un
  // dixième de ce qu'on avait sous les yeux jusqu'à la première interaction.
  apply();
```

- [ ] **Step 2 : Vérifier**

Run: `cd site && npm run build && npm run dev`

Sur `/villemoirieu/` :
1. Taper « Crémieu », recharger la page : le champ « Point de départ » contient Crémieu, les tuiles portent leurs temps de trajet, **aucun chip n'est actif**, le compteur affiche le catalogue entier.
2. Ouvrir la carte : l'épingle 🏠 est là.
3. Aller sur `/pont-de-salars/` : le champ est vide, aucun temps affiché.
4. « Tout réafficher », puis recharger : le champ est vide — la mémoire a bien été effacée.
5. Dans un onglet de navigation privée avec le stockage bloqué : la page se charge normalement.

- [ ] **Step 3 : Commit**

```bash
git add "site/src/pages/[city]/index.astro"
git commit -m "origine : mémoriser la commune de départ d'une visite à l'autre

Le nom seul, jamais les coordonnées — le navigateur mémorise déjà l'autorisation
de géolocalisation, donc persister un point GPS n'achèterait qu'un clic au prix
d'une donnée sensible sur le disque du visiteur.

Restaurée uniquement si la ville correspond : une commune du Lévézou n'a aucun
sens dans le nord-Isère. Et elle ne pose aucun filtre.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Passe de vérification et trace en roadmap

**Files:**
- Modify: `docs/ROADMAP.md`

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces: rien.

- [ ] **Step 1 : Dérouler la liste de vérification de la spec**

Run: `cd site && npm run build && npm test && npm run dev`

Sur `/villemoirieu/`, carte ouverte, cocher les huit points de la spec :

1. Défiler jusqu'aux dernières tuiles : la carte reste visible et ne recouvre ni la barre de recherche ni les titres de section.
2. Géolocalisation refusée : aucun marqueur de position, le message renvoie vers « Point de départ », lequel prend le focus.
3. Saisir « Crémieu » : épingle 🏠, temps de trajet sur les tuiles sans aucun filtre actif, pastille `🏠 Crémieu` près du compteur.
4. Recharger : Crémieu est toujours là, aucun filtre n'est actif.
5. Survoler une tuile hors cadre : la carte s'y déplace, le bon point grossit.
6. Cliquer un point : la bonne tuile se surligne et défile **sous** la carte, pas derrière.
7. À 390 px de large : la carte ne prend pas plus de ~28 % de la hauteur, la bascule 🗺️ reste atteignable.
8. Changer la largeur autour de 1040 px et ouvrir/fermer la colonne de réglages : aucune tuile grise.

Vérifier aussi les deux villes : `/pont-de-salars/` doit se comporter à l'identique.

Tout point en échec se corrige ici, dans un commit dédié, avant l'étape suivante.

- [ ] **Step 2 : Consigner la fonctionnalité en roadmap**

Dans `docs/ROADMAP.md`, à la suite de la ligne existante sur la commune de départ (`:70`), ajouter :

```markdown
- [x] **Carte collante et position du visiteur** : la carte reste à l'écran
      pendant qu'on parcourt les tuiles, un bouton 📍 y pose la position
      (géolocalisée ou commune saisie), et survoler une tuile met son point en
      évidence. Le temps de trajet s'affiche dès qu'une origine est connue, sans
      qu'il faille poser un filtre. Seul le nom de la commune est mémorisé —
      jamais les coordonnées.
```

- [ ] **Step 3 : Commit**

```bash
git add docs/ROADMAP.md
git commit -m "roadmap : carte collante et position du visiteur

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Self-review

**Couverture de la spec** — chaque section a sa tâche :

| Section de la spec | Tâche |
|---|---|
| Le bandeau (sticky, `--top-h`, `--sticky-h`, hauteurs, fond opaque, `ResizeObserver`) | 4 |
| Le bouton 📍 (quatre états, échec, précision aberrante, commune → geoloc) | 5 |
| Origine typée + marqueurs distincts + cercle de précision | 1, 3, 5 |
| Survol tuile → carte (WeakMap, anti-rebond, pas de pan en glissement, tactile, clavier) | 7 |
| Temps de trajet sans filtre | 6 |
| Recadrage et origine lointaine (`pad(0.5)`) | 3 |
| Mémoire (clé, par ville, pas de filtre, `try/catch`, effacements) | 1, 5, 8 |
| `carte.js` / `origine.js` / frontière des `items` | 1, 2, 3 |
| Liste de vérification en 8 points | 9 |

**Cohérence des noms entre tâches** — vérifiée : `creerCarte`, `afficher`, `poserOrigine`, `centrerSurOrigine`, `survoler`, `redimensionner`, `grouperPoints`, `chercherCommune`, `geolocaliser`, `memoriser`, `restaurer`, `oublier` (importé en `oublierOrigine` pour ne pas heurter celui de `prefs.js`), `origine`, `appliquerOrigine`, `assurerOrigine`, `majBoutonPosition`, `messageOrigine`, `majHauteursCollantes`, `entreeDe`, `survolerDepuis`, `itemsDe`, `mesurer`.

**Deux pièges d'ordre signalés dans les tâches, à ne pas rater :**
- Tâche 4, étape 1 : `mapWrap` doit être déclaré **avant** le bloc de mesure — les deux `const` sont à remonter.
- Tâche 5, étape 6 : `carte.poserOrigine` doit être rappelé à l'ouverture de la carte, sinon une commune saisie avant reste sans marqueur.

**Limite assumée :** le dépôt n'a pas de harnais de test navigateur. `node --test` couvre la logique pure (`chercherCommune`, `geolocaliser`, la mémoire, `grouperPoints`) ; tout ce qui touche au DOM, à Leaflet et au CSS collant se vérifie à la main, d'où les listes de contrôle détaillées à chaque tâche.
