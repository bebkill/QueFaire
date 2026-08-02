/**
 * Recherche en langage naturel, côté client.
 *
 * Transforme « je cherche une sortie en famille ce week-end » en un filtre
 * structuré { dateRange, audience, free, categories, communes, text, nearMe }.
 * Zéro dépendance, zéro serveur : tout tourne dans le navigateur.
 */

export function fold(s) {
  return (s || '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/['\u2019-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

const DAY = 24 * 60 * 60 * 1000;
const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);

/** Prochain jour de semaine donné (0=dim … 6=sam), aujourd'hui inclus. */
function nextDow(from, dow) {
  const d = new Date(from);
  d.setDate(d.getDate() + ((dow - d.getDay() + 7) % 7));
  return d;
}

const DATE_PATTERNS = [
  { re: /aujourd ?hui|ce soir|cette nuit/, range: (t) => [iso(t), iso(t)] },
  { re: /demain/, range: (t) => [iso(new Date(t.getTime() + DAY)), iso(new Date(t.getTime() + DAY))] },
  {
    re: /week[ -]?end|ouikende?/,
    range: (t) => {
      const sat = nextDow(t, 6);
      // Vendredi soir compte déjà comme le week-end.
      const start = t.getDay() === 5 ? t : sat;
      return [iso(start), iso(new Date(sat.getTime() + DAY))];
    },
  },
  { re: /cette semaine/, range: (t) => [iso(t), iso(nextDow(t, 0))] },
  { re: /semaine prochaine/, range: (t) => {
      const mon = nextDow(new Date(t.getTime() + DAY), 1);
      return [iso(mon), iso(new Date(mon.getTime() + 6 * DAY))];
    } },
  { re: /ce mois|dans le mois/, range: (t) => [iso(t), iso(new Date(t.getTime() + 31 * DAY))] },
  { re: /\bsamedi\b/, range: (t) => [iso(nextDow(t, 6)), iso(nextDow(t, 6))] },
  { re: /\bdimanche\b/, range: (t) => [iso(nextDow(t, 0)), iso(nextDow(t, 0))] },
];

const CATEGORY_SYNONYMS = {
  concert: /concerts?|musique|musical/,
  spectacle: /spectacles?|theatre|danse|cirque|humour/,
  festival: /festivals?/,
  expo: /expos?\b|expositions?|musees?|galeries?/,
  sport: /sports?|match|tournoi|course|trail|velo|escalade/,
  nature: /natures?|randos?|randonnees?|balades?|montagne|lacs?\b/,
  atelier: /ateliers?|stages?|initiations?|cours\b/,
  marche: /marches?\b|brocantes?|vide[ -]greniers?|producteurs|terroir/,
  patrimoine: /patrimoine|visites?|chateaux?|monuments?/,
  jeunesse: /enfants?|jeune public|marionnettes?|contes?/,
  cinema: /cinemas?|cine\b|films?|projections?/,
  conference: /conferences?|debats?|rencontres?/,
  fete: /fetes?\b|feu d.artifice|carnaval|bals?\b/,
};

/** Synonymes des activités PERMANENTES, tenus à part des catégories
 *  d'événements : « musée » doit ramener le musée (ouvert toute l'année) ET les
 *  expositions temporaires, pas choisir entre les deux. Voir `matches`. */
const PLACE_CATEGORY_SYNONYMS = {
  musee: /musees?|galeries?\b|collections?/,
  patrimoine: /chateaux?|monuments?|patrimoine|ruines?|abbayes?|eglises?|fortifications?/,
  'parc-attraction': /parcs? d.attractions?|attractions?\b|zoos?|aquariums?|accrobranches?/,
  'parc-aquatique': /piscines?|parcs? aquatiques?|aquaparks?|baignades?|plages?|lacs?\b/,
  nature: /parcs?\b|jardins?|reserves?|points? de vue|belvederes?/,
  cinema: /cinemas?|cine\b/,
  spectacle: /theatres?|salles? de spectacle/,
  ludotheque: /ludotheques?|jeux de societe|salles? de jeux/,
  marche: /marches?\b|halles?\b/,
  visite: /visites?|curiosites?|oeuvres?\b|street art/,
  'sport-loisir': /bowlings?|patinoires?|golfs?|escalades?|equitations?|escape games?/,
  ferme: /fermes?\b|artisans?|producteurs?|ateliers? d.artisan/,
  'bien-etre': /thermes?|spas?\b|bien etre/,
};

const AUDIENCE_SYNONYMS = {
  famille: /familles?|familial|avec (mes|les) enfants|sortie famille/,
  enfants: /enfants?|jeune public|petits/,
  ados: /ados?\b|adolescents?/,
  seniors: /seniors?|aines/,
};

/** Estimation de temps de trajet « à vol d'oiseau corrigé ».
 *  Étalonnée sur des trajets types (1 km à pied ≈ 14 min, 5 km vélo ≈ 25 min,
 *  30 km voiture ≈ 40 min) : détour par mode, et vitesse voiture progressive
 *  (urbain lent sur les premiers km, route au-delà). Un vrai moteur isochrone
 *  (OpenRouteService/Valhalla) est prévu en roadmap ; la précision reste de
 *  toute façon bornée par le géocodage à la commune. */
export const SPEEDS = { walk: 4.8, bike: 15, car: 35 };
const DETOUR = { walk: 1.15, bike: 1.25, car: 1.35 };

export function travelMinutes(km, mode = 'car') {
  let speed = SPEEDS[mode] || SPEEDS.car;
  if (mode === 'car') {
    // Vitesse moyenne croissante avec la distance : 30 km/h en ville,
    // jusqu'à ~65 km/h quand le trajet passe par la route.
    speed = Math.min(65, 30 + km);
  }
  return ((km * (DETOUR[mode] || 1.3)) / speed) * 60;
}

/** Arrondi honnête : à 5 min près au-delà de 10 min — la précision réelle
 *  (centre de commune) ne justifie pas mieux. */
export function roundMinutes(min) {
  return min > 10 ? Math.round(min / 5) * 5 : Math.max(1, Math.round(min));
}

const TIME_RE = /a moins d[e'] ?(\d+) ?(min(?:utes?)?|mn|h(?:eures?)?)/;
const MODE_RES = [
  ['walk', /a pied|en marchant|balade|randonnee|rando\b/],
  ['bike', /en velo|a velo|en bicyclette/],
  ['car', /en voiture|en auto/],
];

const STOPWORDS = new Set(
  ('je cherche une un des de du la le les et ou a au aux pour avec dans sur ce cet cette moi me mon ma mes ' +
    'qui que quoi quel quelle quels quelles quand comment est sont suis peux peut peuvent vais va aller ' +
    'nous vous il ils elle elles on se sa son ses leur leurs si pas ne non oui plus tres tout toute tous toutes comme ' +
    'faire voir sortie sorties activite activites idee idees quelque chose truc trucs envie propose ' +
    'montre affiche donne veux voudrais aimerais bien pres proche autour ' +
    'gratuit gratuite gratuits famille familial enfant enfants ado ados weekend week end hui aujourd').split(' ')
);

/**
 * @param {string} query  requête libre de l'utilisateur
 * @param {string[]} communes  communes connues du secteur (pour la détection)
 * @param {Date} [now]
 */
export function parseQuery(query, communes = [], now = new Date()) {
  const q = fold(query);
  let rest = q; // ce qui n'est pas consommé par un motif devient du plein texte
  const consume = (re) => {
    rest = rest.replace(new RegExp(re.source, re.flags.includes('g') ? re.flags : re.flags + 'g'), ' ');
  };
  const filter = {
    dateFrom: null,
    dateTo: null,
    categories: [],
    placeCategories: [],
    audience: [],
    free: null,
    communes: [],
    nearMe: false,
    maxMinutes: null,
    mode: 'car',
    modeExplicit: false,
    // null = événements ET activités ; 'event' / 'place' = un seul type.
    kind: null,
    unusual: false,
    minRating: null,
    text: '',
  };

  const time = q.match(TIME_RE);
  if (time) {
    filter.maxMinutes = parseInt(time[1], 10) * (time[2].startsWith('h') ? 60 : 1);
    consume(TIME_RE);
  }
  for (const [mode, re] of MODE_RES) {
    if (re.test(q)) {
      filter.mode = mode;
      filter.modeExplicit = true; // « balade » implique la marche, « en voiture » etc.
      break;
    }
  }
  consume(/a pied|en marchant|en velo|a velo|en bicyclette|en voiture|en auto/);

  for (const { re, range } of DATE_PATTERNS) {
    if (re.test(q)) {
      [filter.dateFrom, filter.dateTo] = range(now);
      consume(re);
      break;
    }
  }

  // « Insolite » et « bien noté » ne concernent que les activités permanentes
  // (un événement n'a pas de note d'avis) : ils impliquent donc le type.
  const unusualRe = /insolites?|atypiques?|meconnus?|originaux?|originales?|hors des sentiers|curiosites?|secrets?\b/;
  if (unusualRe.test(q)) {
    filter.unusual = true;
    filter.kind = 'place';
    consume(unusualRe);
  }
  const ratedRe = /bien notes?|bien notees?|mieux notes?|meilleures?\b|tres bien notes?|top\b/;
  if (ratedRe.test(q)) {
    filter.minRating = 4;
    filter.kind = 'place';
    consume(ratedRe);
  }
  const permanentRe = /activites? permanentes?|toute l annee|permanents?\b|a visiter/;
  if (permanentRe.test(q)) {
    filter.kind = 'place';
    consume(permanentRe);
  }

  for (const [cat, re] of Object.entries(CATEGORY_SYNONYMS)) {
    if (re.test(q)) {
      filter.categories.push(cat);
      consume(re);
    }
  }
  for (const [cat, re] of Object.entries(PLACE_CATEGORY_SYNONYMS)) {
    if (re.test(q)) {
      filter.placeCategories.push(cat);
      consume(re);
    }
  }
  for (const [aud, re] of Object.entries(AUDIENCE_SYNONYMS)) {
    if (re.test(q)) {
      filter.audience.push(aud);
      consume(re);
    }
  }
  if (/gratuit/.test(q)) {
    filter.free = true;
    consume(/gratuite?s?/);
  }
  const nearRe = /(pres|proche|autour) de (chez )?moi|a proximite|a cote de chez moi/;
  if (nearRe.test(q)) {
    filter.nearMe = true;
    consume(nearRe);
  }

  for (const commune of communes) {
    const f = fold(commune);
    if (f.length > 3 && q.includes(f)) {
      filter.communes.push(commune);
      rest = rest.split(f).join(' ');
    }
  }

  // Le reste devient une recherche plein texte.
  filter.text = rest
    .replace(/[^a-z0-9 ]/g, ' ')
    .split(/\s+/)
    .filter((w) => w.length > 2 && !STOPWORDS.has(w))
    .join(' ')
    .trim();

  return filter;
}

/** Distance haversine en km. */
export function distanceKm(lat1, lon1, lat2, lon2) {
  const rad = Math.PI / 180;
  const a =
    Math.sin(((lat2 - lat1) * rad) / 2) ** 2 +
    Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(((lon2 - lon1) * rad) / 2) ** 2;
  return 6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/**
 * Teste une fiche (attributs data- de la carte) contre un filtre.
 * Couvre les deux types : `kind === 'place'` pour une activité permanente,
 * `'event'` (défaut) pour un événement daté.
 * @param {{kind?:string, date:string, end:string, cat:string, commune:string,
 *          audience:string, free:string, rating?:string, unusual?:string,
 *          text:string}} ev
 */
export function matches(ev, filter) {
  const isPlace = ev.kind === 'place';
  if (filter.kind && filter.kind !== (isPlace ? 'place' : 'event')) return false;

  // Une activité permanente est ouverte toute l'année : un filtre de dates ne
  // la disqualifie pas — « que faire ce week-end » inclut légitimement le musée.
  if (filter.dateFrom && !isPlace) {
    const start = ev.date;
    const end = ev.end || ev.date;
    // Chevauchement de périodes : l'événement doit croiser [dateFrom, dateTo].
    if (end < filter.dateFrom || start > filter.dateTo) return false;
  }

  if (filter.unusual && ev.unusual !== 'true') return false;
  if (filter.minRating != null && !(parseFloat(ev.rating) >= filter.minRating)) return false;

  // Chaque type est jugé sur SON jeu de catégories. Dès qu'une contrainte de
  // catégorie existe, une fiche dont le type n'est visé par aucune est écartée :
  // « concert » ne doit pas ramener les musées.
  const catFilter = isPlace ? filter.placeCategories : filter.categories;
  if ((filter.categories.length || filter.placeCategories.length) && !catFilter.includes(ev.cat)) {
    return false;
  }

  if (filter.audience.length) {
    const evAud = ev.audience.split(' ');
    const ok = filter.audience.some((a) => evAud.includes(a) || evAud.includes('tous'));
    if (!ok) return false;
  }
  if (filter.free === true && ev.free !== 'true') return false;
  if (filter.communes.length && !filter.communes.some((c) => fold(c) === fold(ev.commune))) {
    return false;
  }
  if (filter.text) {
    const words = filter.text.split(' ');
    const hay = ev.text;
    if (!words.every((w) => hay.includes(w))) return false;
  }
  return true;
}
