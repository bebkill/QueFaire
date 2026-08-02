/**
 * Classement et plafonnement des activités permanentes à l'affichage.
 *
 * Un rayon d'une heure autour d'un épicentre rural ramène ~2600 activités
 * (Pont-de-Salars, run réel). Toutes les rendre produit une page de 3,7 Mo,
 * inutilisable sur mobile — et noie les 36 événements du secteur sous les
 * activités, alors que le daté doit primer.
 *
 * On plafonne donc l'AFFICHAGE, pas la collecte : places.json reste complet
 * (c'est lui qui porte l'historique et l'enrichissement déjà payé), seules les
 * mieux classées sont rendues. Le compte des non affichées est indiqué au
 * visiteur — un plafond silencieux ferait passer une troncature pour une
 * exhaustivité.
 */

/** Nombre d'activités rendues par ville. ~300 tuiles ≈ page de 500 Ko. */
export const MAX_RENDERED = 300;

const NOTABLE = new Set([
  'unesco', 'monument-historique', 'musee-de-france', 'jardin-remarquable',
  'maisons-des-illustres', 'art-et-histoire', 'qualite-tourisme', 'tourisme-handicap',
]);

/**
 * Score d'intérêt d'une activité, fondé uniquement sur des propriétés
 * INTRINSÈQUES : distinction officielle, horaires, site officiel, description.
 *
 * La présentation LLM (`tldr`) en est délibérément ABSENTE. L'inclure rendait
 * le classement circulaire : la même fonction choisissait qui présenter et se
 * trouvait modifiée par la présentation. Une fiche présentée gagnait des points,
 * montait, et en délogeait une autre — restée sans phrase. Résultat mesuré :
 * 385 phrases payées jamais affichées, et 74 activités affichées sans phrase.
 * Avec un score stable, l'ensemble affiché ne bouge plus et la file de
 * présentation le remplit en un passage.
 *
 * `unusual` n'y figure pas non plus, pour la même raison : il vient du LLM.
 */
export function placeScore(p) {
  const quality = p.quality || [];
  return (
    (quality.some((c) => NOTABLE.has(c)) ? 100 : 0) +
    (p.opening_hours ? 15 : 0) +
    (p.url ? 10 : 0) +
    (p.description ? 5 : 0) +
    (quality.includes('notoriete') ? 5 : 0) +
    // Deux fournisseurs qui décrivent le même lieu : signal de réalité.
    ((p.providers || []).length > 1 ? 5 : 0)
  );
}

/** Les `limit` activités les plus intéressantes, à score égal par ordre alphabétique. */
export function rankPlaces(places, limit = MAX_RENDERED) {
  return [...places]
    .sort(
      (a, b) =>
        placeScore(b) - placeScore(a) ||
        // À score égal seulement : l'enrichissement LLM départage, sans jamais
        // pouvoir faire entrer ou sortir une fiche de l'ensemble affiché.
        Number(Boolean(b.unusual)) - Number(Boolean(a.unusual)) ||
        Number(Boolean(b.tldr)) - Number(Boolean(a.tldr)) ||
        a.name.localeCompare(b.name, 'fr')
    )
    .slice(0, limit);
}
