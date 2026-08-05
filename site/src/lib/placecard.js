/** Tuile d'activité permanente, construite dans le NAVIGATEUR.
 *
 * Pourquoi pas un composant Astro comme pour les événements : pré-rendre les
 * 2232 tuiles d'une ville produisait 3,7 Mo de HTML, d'où un plafond de 300
 * fiches rendues — un plafond posé par le poids de la page, pas par l'intérêt
 * des activités, qui écartait donc arbitrairement 1900 fiches de la recherche.
 * En embarquant les données maigres (0,35 Mo compressés pour TOUT le catalogue)
 * et en ne fabriquant que les tuiles de la page affichée, le plafond disparaît :
 * recherche, filtres et tri portent sur l'intégralité du catalogue.
 *
 * Ce fichier est le SEUL rendu des tuiles d'activité. `PlaceCard.astro` a été
 * supprimé plutôt que conservé « au cas où » : deux rendus du même objet
 * divergent, ce projet en a déjà fait l'expérience avec le score d'affichage
 * dupliqué entre Python et JavaScript.
 */

import { PLACE_EMOJI, isNotable, openingLabel, qualityBadges, ratingStars } from './format.js';
import { fold } from './nlsearch.js';

const el = (balise, classe, texte) => {
  const n = document.createElement(balise);
  if (classe) n.className = classe;
  if (texte != null) n.textContent = texte;
  return n;
};

/**
 * @param {object} p       fiche maigre (voir `champsTuile` dans [city]/index.astro)
 * @param {object} ctx     { categories, qualityLabels, cityBase }
 * @returns {HTMLAnchorElement}
 */
export function carteActivite(p, ctx) {
  const label = ctx.categories?.[p.category] || 'Activité';
  const badges = qualityBadges(p.quality);
  const opening = openingLabel(p.opening_hours);
  const adultes = (p.audience || []).includes('adultes');
  // Avec un site officiel on envoie chez l'exploitant — c'est lui qui sait ses
  // horaires et ses tarifs. Sans site, la fiche de détail interne prend le relais.
  const externe = Boolean(p.url);

  const a = el('a', 'card card-place');
  a.href = externe ? p.url : `${ctx.cityBase}activite/${p.id}/`;
  if (externe) {
    a.target = '_blank';
    a.rel = 'noopener';
  }
  a.style.setProperty('--cat', `var(--p-${p.category})`);

  const haut = el('div', 'card-top');
  const emoji = el('span', 'emoji', PLACE_EMOJI[p.category] || '📍');
  emoji.setAttribute('aria-hidden', 'true');
  const perm = el('span', 'perm-badge', 'Permanent');
  perm.title = "Activité ouverte toute l'année, pas un événement daté";
  haut.append(emoji, el('span', 'cat-label', label), perm);

  const corps = el('div', 'card-body');
  const titre = el('h3', null, p.name);
  if (externe) {
    const fleche = el('span', 'ext', '↗');
    fleche.setAttribute('aria-label', "ouvre le site de l'activité");
    titre.appendChild(fleche);
  }
  corps.appendChild(titre);

  // Photo réservée aux fiches sans site officiel : ce sont celles dont la tuile
  // est le seul aperçu, et 2232 images chargées d'un coup seraient ruineuses.
  if (!externe && p.image_url) {
    const img = el('img', 'card-photo');
    img.src = p.image_url;
    img.alt = `Photo de ${p.name}`;
    img.loading = 'lazy';
    img.decoding = 'async';
    corps.appendChild(img);
  }

  if (badges.length) {
    const ligne = el('p', 'quality-badges');
    for (const b of badges) {
      const badge = el('span', 'q-badge');
      badge.title = ctx.qualityLabels?.[b.code] || b.short;
      const ico = el('span', null, b.emoji);
      ico.setAttribute('aria-hidden', 'true');
      badge.append(ico, document.createTextNode(` ${b.short}`));
      ligne.appendChild(badge);
    }
    corps.appendChild(ligne);
  }

  if (p.unusual) corps.appendChild(el('p', 'unusual-tag', '✨ Insolite — hors des sentiers battus'));
  if (p.tldr) corps.appendChild(el('p', 'tldr', `💡 ${p.tldr}`));
  else if (p.description) corps.appendChild(el('p', 'desc', p.description));
  if (opening) corps.appendChild(el('p', 'opening', `🕒 ${opening}`));

  const meta = el('div', 'card-meta');
  if (p.commune) meta.appendChild(el('span', null, `📍 ${p.commune}`));
  if (adultes) {
    const maj = el('span', 'adults', '18+');
    maj.title = 'Activité réservée aux personnes majeures';
    meta.appendChild(maj);
  }
  if (p.rating != null) {
    const note = el('span', 'rating');
    const source = p.rating_source === 'tripadvisor' ? 'TripAdvisor' : 'Google';
    note.title = `${p.rating}/5 sur ${source}${p.rating_count ? ` — ${p.rating_count} avis` : ''}`;
    const etoiles = el('span', 'stars', ratingStars(p.rating));
    etoiles.setAttribute('aria-hidden', 'true');
    note.append(etoiles, document.createTextNode(p.rating.toFixed(1)));
    if (p.rating_count) note.appendChild(el('span', 'rating-count', `(${p.rating_count})`));
    meta.appendChild(note);
  }
  if (p.fee === false) meta.appendChild(el('span', 'free', 'Gratuit'));
  const dist = el('span', 'dist');
  dist.dataset.role = 'distance';
  meta.appendChild(dist);

  corps.appendChild(meta);
  a.append(haut, corps);
  return a;
}

/** Texte de recherche replié, calculé une fois puis mémorisé sur la fiche.
 *
 *  Reconstruit ici plutôt qu'embarqué : dans le blob, il recopiait nom,
 *  présentation, description et commune — 112 Ko compressés de duplication pour
 *  2232 fiches, le poste le plus lourd. Le coût d'un `fold()` par fiche est payé
 *  une seule fois, au premier filtrage.
 */
function texteRecherche(p, categories) {
  if (p._s === undefined) {
    p._s = fold([
      p.name, p.tldr, p.description, p.commune,
      categories?.[p.category] || '',
      p.unusual ? 'insolite' : '',
      qualityBadges(p.quality, 6).map((b) => b.short).join(' '),
    ].join(' '));
  }
  return p._s;
}

/** Données de filtrage d'une fiche, au même format que `cardData` pour un
 *  événement — c'est ce qui permet à `matches()` de juger les deux sans savoir
 *  lequel vient du DOM et lequel vient d'un blob JSON. */
export function donneesActivite(p, categories) {
  return {
    kind: 'place',
    date: '', end: '',
    cat: p.category,
    commune: p.commune || '',
    audience: (p.audience || ['tous']).join(' '),
    free: String(p.fee === false),
    rating: p.rating ?? '',
    unusual: String(p.unusual === true),
    notable: String(isNotable(p.quality)),
    text: texteRecherche(p, categories),
    lat: p.lat ?? NaN,
    lon: p.lon ?? NaN,
  };
}
