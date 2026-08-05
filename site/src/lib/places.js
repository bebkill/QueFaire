/**
 * Classement des activités permanentes : il définit l'ordre « pertinence ».
 *
 * Il n'y a PLUS de plafond. Un rayon d'une heure autour d'un épicentre rural
 * ramène ~2200 activités, et les pré-rendre toutes en HTML donnait une page de
 * 3,7 Mo — d'où un plafond de 300 qui écartait 1900 fiches de la recherche pour
 * une raison de poids de page. Le navigateur ne fabrique désormais que les tuiles
 * de la page affichée (`lib/placecard.js`), donc tout le catalogue est cherchable
 * et c'est au visiteur, non à un score de documentation, de décider ce qui compte.
 */

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

/** TOUTES les activités, de la plus documentée à la moins, alphabétique à égalité.
 *
 *  Ne tronque plus. Le plafond de 300 tenait au pré-rendu HTML de chaque tuile ;
 *  depuis que le navigateur ne fabrique que la page affichée
 *  (`lib/placecard.js`), il n'a plus de raison d'être — et il en avait une
 *  mauvaise : écarter 1900 activités de la RECHERCHE pour une contrainte de
 *  poids de page, alors que ce qui doit trier ce sont les préférences du
 *  visiteur.
 *
 *  Ce classement reste utile : il définit l'ordre « pertinence » par défaut.
 */
export function rankPlaces(places) {
  return [...places].sort(
    (a, b) =>
      placeScore(b) - placeScore(a) ||
      // À score égal seulement : l'enrichissement LLM départage, sans jamais
      // pouvoir faire remonter une fiche au-dessus d'une mieux documentée.
      Number(Boolean(b.unusual)) - Number(Boolean(a.unusual)) ||
      Number(Boolean(b.tldr)) - Number(Boolean(a.tldr)) ||
      a.name.localeCompare(b.name, 'fr')
  );
}
