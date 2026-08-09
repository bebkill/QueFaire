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
