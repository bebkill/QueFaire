// Accès aux données par ville, générées par le pipeline sous
// src/data/cities/<id>/{events,places,sector}.json. Un seul build rassemble
// toutes les villes crawlées ; les routes [city] s'appuient dessus
// (getStaticPaths).
//
// events.json vient du crawl (2×/jour), places.json de la découverte des
// activités permanentes (hebdomadaire) : une ville peut donc parfaitement avoir
// des événements sans encore avoir d'activités — d'où le repli sur [].

const sectorMods = import.meta.glob('../data/cities/*/sector.json', { eager: true });
const eventMods = import.meta.glob('../data/cities/*/events.json', { eager: true });
const placeMods = import.meta.glob('../data/cities/*/places.json', { eager: true });

const idFromPath = (p) => p.match(/cities\/([^/]+)\//)[1];

const sectors = {};
for (const [path, mod] of Object.entries(sectorMods)) sectors[idFromPath(path)] = mod.default;

const events = {};
for (const [path, mod] of Object.entries(eventMods)) events[idFromPath(path)] = mod.default;

const places = {};
for (const [path, mod] of Object.entries(placeMods)) places[idFromPath(path)] = mod.default;

/** Identifiants des villes disposant de données (crawlées). */
export const cityIds = Object.keys(sectors).sort();

export const getSector = (id) => sectors[id];
export const getEvents = (id) => events[id] || [];
export const getPlaces = (id) => places[id] || [];
