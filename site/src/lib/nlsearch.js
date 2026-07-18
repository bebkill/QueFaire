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

const AUDIENCE_SYNONYMS = {
  famille: /familles?|familial|avec (mes|les) enfants|sortie famille/,
  enfants: /enfants?|jeune public|petits/,
  ados: /ados?\b|adolescents?/,
  seniors: /seniors?|aines/,
};

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
    audience: [],
    free: null,
    communes: [],
    nearMe: false,
    text: '',
  };

  for (const { re, range } of DATE_PATTERNS) {
    if (re.test(q)) {
      [filter.dateFrom, filter.dateTo] = range(now);
      consume(re);
      break;
    }
  }

  for (const [cat, re] of Object.entries(CATEGORY_SYNONYMS)) {
    if (re.test(q)) {
      filter.categories.push(cat);
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
 * Teste un événement (attributs data- de la carte) contre un filtre.
 * @param {{date:string, end:string, cat:string, commune:string, audience:string,
 *          free:string, text:string}} ev
 */
export function matches(ev, filter) {
  if (filter.dateFrom) {
    const start = ev.date;
    const end = ev.end || ev.date;
    // Chevauchement de périodes : l'événement doit croiser [dateFrom, dateTo].
    if (end < filter.dateFrom || start > filter.dateTo) return false;
  }
  if (filter.categories.length && !filter.categories.includes(ev.cat)) return false;
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
