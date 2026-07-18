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
