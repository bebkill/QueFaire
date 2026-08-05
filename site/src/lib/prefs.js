/** Goûts du visiteur, appris par 👍/👎 — sans compte, sans serveur, sans cookie.
 *
 * Pourquoi pas un questionnaire d'accueil : quatre questions avant d'avoir rien
 * vu, c'est un péage. Deux avis sur des activités CONCRÈTES en disent plus, et se
 * donnent en passant.
 *
 * Ce qu'un vote apprend. Une activité porte peu de traits exploitables — sa
 * catégorie, son caractère insolite, sa distinction, sa gratuité — mais c'est
 * assez pour généraliser : 👍 sur un musée fait remonter les musées, 👎 sur une
 * chapelle fait descendre le patrimoine. Le modèle est volontairement une simple
 * somme de poids, PAS un classifieur : il doit pouvoir s'expliquer en une phrase
 * au visiteur (`resume()`), sinon « QueFaire a compris ce que je veux » devient
 * une boîte noire dans laquelle on n'a aucune raison d'avoir confiance.
 *
 * RÈGLE : les préférences RÉORDONNENT, elles ne masquent jamais. Sur deux ou
 * trois avis on ne sait presque rien ; cacher des activités sur cette base
 * transformerait une intuition en censure. Le visiteur garde tout sous les yeux,
 * simplement dans un ordre qui lui ressemble.
 *
 * Stockage : `localStorage`, une seule clé, partagée entre les villes — les goûts
 * d'une personne ne changent pas quand elle passe de l'Aveyron à l'Isère. C'est
 * la première brique du « fichier de préférences » de la roadmap (export/import),
 * et aucune donnée ne quitte le navigateur.
 */

const CLE = 'quefaire:prefs:v1';

/** Traits d'une activité sur lesquels un vote se généralise. */
export function traits(p) {
  const t = [`cat:${p.category}`];
  if (p.unusual) t.push('insolite');
  if (p.quality && p.quality.some((q) => q !== 'notoriete')) t.push('distingue');
  if (p.fee === false) t.push('gratuit');
  return t;
}

export function charger() {
  try {
    const brut = localStorage.getItem(CLE);
    const d = brut ? JSON.parse(brut) : null;
    if (d && typeof d.poids === 'object') return { poids: d.poids, votes: d.votes || {} };
  } catch {
    // localStorage refusé (navigation privée, réglage strict) ou contenu abîmé :
    // on repart de zéro plutôt que de casser la page. La personnalisation est un
    // agrément, jamais un prérequis.
  }
  return { poids: {}, votes: {} };
}

function enregistrer(prefs) {
  try {
    localStorage.setItem(CLE, JSON.stringify(prefs));
  } catch {
    /* quota ou stockage interdit : le vote reste valable pour cette visite */
  }
}

/**
 * Enregistre un avis. `sens` vaut +1 (👍) ou -1 (👎).
 *
 * Le poids de la CATÉGORIE bouge de 2, celui des autres traits de 1 : c'est le
 * trait le plus discriminant, et celui que le visiteur reconnaîtra dans le
 * résumé. Revoter sur la même activité remplace l'avis précédent au lieu de
 * l'empiler — sinon cliquer deux fois compterait double.
 */
export function voter(prefs, p, sens) {
  const ancien = prefs.votes[p.id];
  if (ancien === sens) return prefs;
  for (const t of traits(p)) {
    const pas = t.startsWith('cat:') ? 2 : 1;
    if (ancien) prefs.poids[t] = (prefs.poids[t] || 0) - ancien * pas; // on défait
    prefs.poids[t] = (prefs.poids[t] || 0) + sens * pas;
    if (prefs.poids[t] === 0) delete prefs.poids[t];
  }
  prefs.votes[p.id] = sens;
  enregistrer(prefs);
  return prefs;
}

export function oublier() {
  try {
    localStorage.removeItem(CLE);
  } catch { /* rien à faire */ }
  return { poids: {}, votes: {} };
}

export const nbVotes = (prefs) => Object.keys(prefs.votes).length;

/** Score de goût d'une activité. 0 quand on ne sait rien — l'ordre d'origine
 *  reprend alors la main, ce qui est le bon comportement au premier chargement. */
export function scorePrefs(prefs, p) {
  let s = 0;
  for (const t of traits(p)) s += prefs.poids[t] || 0;
  // Un avis déjà donné compte double : ce que le visiteur a aimé doit remonter
  // même si sa catégorie est par ailleurs mal notée.
  const vote = prefs.votes[p.id];
  if (vote) s += vote * 3;
  return s;
}

/** Ce que QueFaire a compris, en français, pour que le visiteur puisse le
 *  vérifier — et le contredire. Rendre le modèle lisible est la condition de la
 *  confiance ; un score opaque ne se conteste pas. */
export function resume(prefs, libelles) {
  const nom = (t) =>
    t.startsWith('cat:') ? (libelles?.[t.slice(4)] || t.slice(4)).toLowerCase()
      : t === 'insolite' ? 'les activités insolites'
        : t === 'distingue' ? 'les lieux distingués'
          : 'la gratuité';
  const tries = Object.entries(prefs.poids).sort((a, b) => b[1] - a[1]);
  const plus = tries.filter(([, v]) => v > 0).slice(0, 3).map(([t]) => nom(t));
  const moins = tries.filter(([, v]) => v < 0).slice(-2).map(([t]) => nom(t));
  const bouts = [];
  if (plus.length) bouts.push(`vous aimez ${plus.join(', ')}`);
  if (moins.length) bouts.push(`moins ${moins.join(' et ')}`);
  return bouts.join(' — ');
}

/**
 * Quelques activités à soumettre au vote, choisies pour APPRENDRE VITE.
 *
 * Une par catégorie, et les mieux placées d'abord : proposer cinq musées
 * n'apprendrait rien, alors qu'un musée, une ferme et un site naturel séparent
 * déjà trois goûts. On écarte ce qui a déjà été jugé, et on exige une fiche
 * présentable — sans photo ni description, le visiteur ne peut pas se prononcer,
 * et son avis porterait sur le vide.
 */
export function aProposer(prefs, places, combien = 3) {
  // Catégories DÉJÀ jugées, y compris lors des tours précédents. Sans cette
  // mémoire, on reproposait un château après un château : le visiteur répondait
  // deux fois à la même question, et un 👍 suivi d'un 👎 sur la même catégorie
  // ramenait son poids à zéro — deux clics pour n'apprendre rien. Observé au test.
  const jugees = new Set();
  for (const p of places) {
    if (prefs.votes[p.id]) jugees.add(p.category);
  }
  const choix = [];
  for (const p of places) {
    if (prefs.votes[p.id] || jugees.has(p.category)) continue;
    if (!p.tldr && !p.description && !p.image_url) continue;
    jugees.add(p.category);
    choix.push(p);
    if (choix.length === combien) break;
  }
  return choix;
}
