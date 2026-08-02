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

/** Note sur 5 → étoiles pleines/demi/vides, lisible sans CSS particulier. */
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
