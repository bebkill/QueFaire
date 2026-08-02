/** Aides d'affichage partagées entre les pages Astro. */

export const CATEGORY_EMOJI = {
  concert: '🎵',
  spectacle: '🎭',
  festival: '🎪',
  expo: '🖼️',
  sport: '🏃',
  nature: '🌿',
  atelier: '🛠️',
  marche: '🧺',
  patrimoine: '🏰',
  jeunesse: '🧒',
  cinema: '🎬',
  conference: '💬',
  fete: '🎆',
  autre: '📌',
};

/** Activités permanentes : jeu d'icônes distinct des événements, pour qu'un
 *  musée ne se confonde pas avec une expo temporaire dans la grille. */
export const PLACE_EMOJI = {
  musee: '🏛️',
  patrimoine: '🏰',
  'parc-attraction': '🎢',
  'parc-aquatique': '🏊',
  nature: '🌳',
  cinema: '🎬',
  spectacle: '🎭',
  ludotheque: '🎲',
  marche: '🧺',
  visite: '🔭',
  'sport-loisir': '⛳',
  ferme: '🚜',
  'bien-etre': '♨️',
  autre: '📍',
};

/** Signaux de qualité libres (distinctions officielles, notoriété). Ils tiennent
 *  la place de la note d'avis, dont l'affichage est contraint par les CGU des
 *  fournisseurs. Ordre = ordre d'affichage, le plus distinctif d'abord. */
export const QUALITY_BADGES = {
  unesco: { emoji: '🌍', short: 'UNESCO' },
  'monument-historique': { emoji: '🏛️', short: 'Monument Historique' },
  'musee-de-france': { emoji: '🎨', short: 'Musée de France' },
  'jardin-remarquable': { emoji: '🌸', short: 'Jardin remarquable' },
  'maisons-des-illustres': { emoji: '✒️', short: 'Maison des Illustres' },
  'art-et-histoire': { emoji: '🗺️', short: "Pays d'art et d'histoire" },
  'qualite-tourisme': { emoji: '🏅', short: 'Qualité Tourisme' },
  'tourisme-handicap': { emoji: '♿', short: 'Tourisme & Handicap' },
  notoriete: { emoji: '📖', short: 'Wikipédia' },
};

/** Codes qui valent « valeur sûre » : une distinction décernée par un tiers.
 *  La notice Wikipédia atteste d'une notoriété, pas d'une qualité d'accueil —
 *  elle n'en fait pas partie (miroir de NOTABLE_LABELS côté pipeline). */
const NOTABLE = new Set(Object.keys(QUALITY_BADGES).filter((k) => k !== 'notoriete'));

export const isNotable = (quality) => (quality || []).some((c) => NOTABLE.has(c));

/** Badges d'une activité, dans l'ordre d'affichage, limités pour ne pas noyer
 *  la tuile. */
export function qualityBadges(quality, max = 3) {
  return (quality || [])
    .filter((c) => QUALITY_BADGES[c])
    .sort((a, b) => Object.keys(QUALITY_BADGES).indexOf(a) - Object.keys(QUALITY_BADGES).indexOf(b))
    .slice(0, max)
    .map((c) => ({ code: c, ...QUALITY_BADGES[c] }));
}

/** Note sur 5 → étoiles pleines/demi/vides, lisible sans CSS particulier.
 *  Conservé pour un affichage éventuel sur les tuiles ; voir ratings.py pour
 *  les conditions d'affichage imposées par Google et TripAdvisor. */
export function ratingStars(rating) {
  if (rating == null) return '';
  const full = Math.floor(rating);
  const half = rating - full >= 0.25 && rating - full < 0.75;
  const bonus = rating - full >= 0.75 ? 1 : 0;
  return '★'.repeat(full + bonus) + (half ? '½' : '') + '☆'.repeat(Math.max(0, 5 - full - bonus - (half ? 1 : 0)));
}

/** Horaires OSM → texte lisible. La syntaxe OSM (« Tu-Su 10:00-18:00 ») est
 *  imbuvable pour un visiteur : on traduit les jours et les cas courants, et on
 *  laisse passer le reste tel quel plutôt que d'afficher faux. */
const OSM_DAYS = { Mo: 'lun', Tu: 'mar', We: 'mer', Th: 'jeu', Fr: 'ven', Sa: 'sam', Su: 'dim' };

export function openingLabel(hours) {
  if (!hours) return null;
  if (/^24\/7$/.test(hours.trim())) return 'Ouvert en permanence';
  return hours
    .replace(/\b(Mo|Tu|We|Th|Fr|Sa|Su)\b/g, (d) => OSM_DAYS[d])
    .replace(/;\s*/g, ' · ')
    .replace(/,/g, ', ')
    .replace(/\boff\b/g, 'fermé')
    .replace(/\bPH\b/g, 'jours fériés');
}

const MONTHS_SHORT = ['janv', 'févr', 'mars', 'avr', 'mai', 'juin', 'juil', 'août', 'sept', 'oct', 'nov', 'déc'];

export function dateBadge(startIso) {
  const d = new Date(startIso);
  return { day: d.getDate(), mon: MONTHS_SHORT[d.getMonth()] };
}

export function formatWhen(startIso, endIso) {
  const start = new Date(startIso);
  const opts = { weekday: 'long', day: 'numeric', month: 'long' };
  let text = start.toLocaleDateString('fr-FR', opts);
  if (startIso.length > 10 && !(start.getHours() === 0 && start.getMinutes() === 0)) {
    text += ` à ${start.getHours()}h${String(start.getMinutes()).padStart(2, '0')}`;
  }
  if (endIso) {
    const end = new Date(endIso);
    if (end.toDateString() !== start.toDateString()) {
      text += ` → ${end.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long' })}`;
    }
  }
  return text;
}

export function priceLabel(ev) {
  if (ev.free === true) return 'Gratuit';
  if (ev.price_text) return ev.price_text;
  if (ev.free === false) return 'Payant';
  return null;
}
