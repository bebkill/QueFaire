// Accès aux données par ville, générées par le pipeline sous
// src/data/cities/<id>/{events,sector}.json. Un seul build rassemble toutes les
// villes crawlées ; les routes [city] s'appuient dessus (getStaticPaths).

const sectorMods = import.meta.glob('../data/cities/*/sector.json', { eager: true });
const eventMods = import.meta.glob('../data/cities/*/events.json', { eager: true });

const idFromPath = (p) => p.match(/cities\/([^/]+)\//)[1];

const sectors = {};
for (const [path, mod] of Object.entries(sectorMods)) sectors[idFromPath(path)] = mod.default;

const events = {};
for (const [path, mod] of Object.entries(eventMods)) events[idFromPath(path)] = mod.default;

/** Identifiants des villes disposant de données (crawlées). */
export const cityIds = Object.keys(sectors).sort();

export const getSector = (id) => sectors[id];
export const getEvents = (id) => events[id] || [];
