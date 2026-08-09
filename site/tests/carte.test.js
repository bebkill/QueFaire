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
