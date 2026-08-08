/** Le point de départ du visiteur — d'où l'on mesure, et ce qu'on ose dessiner.
 *
 * Une position n'est pas un simple {lat, lon} : elle DIT D'OÙ ELLE VIENT. Un
 * point GPS à 15 m près, le centre d'une commune de 2 km et le centre du secteur
 * ne valent pas la même chose, et surtout ne se dessinent pas pareil — le
 * dernier ne se dessine pas du tout. La page retombait jusqu'ici sur le centre
 * du secteur quand la géolocalisation était refusée : acceptable pour estimer un
 * temps de trajet, mensonger sous un marqueur « Vous êtes ici ». Ce module rend
 * la provenance explicite pour que l'affichage puisse en tenir compte.
 *
 * Aucun réseau : les communes sont embarquées dans `sector.json` (géocodage
 * hors-ligne, quelques Ko). Et seul le NOM d'une commune est mémorisé — jamais
 * des coordonnées. Le navigateur mémorise déjà l'autorisation de
 * géolocalisation, donc persister un point GPS exact n'achèterait qu'un clic, au
 * prix d'une donnée sensible écrite sur le disque du visiteur.
 */
import { fold } from './nlsearch.js';

export const CLE = 'quefaire:origin:v1';

/** Au-delà, ce n'est plus du GPS mais une géolocalisation par IP — souvent le
 *  nœud régional du fournisseur d'accès, à des dizaines de kilomètres. On la
 *  traite comme un échec : un faux point bleu est pire qu'un point absent. */
export const PRECISION_MAX_M = 5000;

const echec = (code) => Object.assign(new Error(code), { code });

/**
 * Retrouve une commune du rayon depuis ce que le visiteur a tapé.
 *
 * Correspondance exacte D'ABORD, préfixe ensuite : sur « Saint-Chef », l'ordre
 * du tableau ferait autrement gagner « Saint-Chef-le-Haut ». `fold` neutralise
 * accents, casse, apostrophes et tirets — « ST-CHEF », « saint chef » et
 * « Saint-Chef » désignent la même commune pour qui la tape de mémoire.
 */
export function chercherCommune(communePoints, saisie) {
  const cle = fold((saisie || '').trim());
  if (!cle) return null;
  const liste = communePoints || [];
  return liste.find((c) => fold(c.nom) === cle)
    || liste.find((c) => fold(c.nom).startsWith(cle))
    || null;
}

/**
 * Demande la position au navigateur.
 *
 * `geo` est injectable pour les tests — c'est la seule couture : `navigator`
 * n'existe pas dans Node, et on ne veut pas d'un module qui ne se teste qu'en
 * navigateur. Haute précision demandée : privilégie wifi/GPS à la géoloc par IP.
 */
export function geolocaliser({ geo = globalThis.navigator?.geolocation } = {}) {
  return new Promise((resolve, reject) => {
    if (!geo) return reject(echec('indisponible'));
    geo.getCurrentPosition(
      (pos) => {
        const { latitude, longitude, accuracy } = pos.coords;
        if (accuracy != null && accuracy > PRECISION_MAX_M) return reject(echec('imprecise'));
        resolve({ lat: latitude, lon: longitude, precision: accuracy ?? null });
      },
      () => reject(echec('refusee')),
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
    );
  });
}

/** La mémoire est LIÉE À LA VILLE : la commune retenue pour l'Aveyron n'a aucun
 *  sens dans le nord-Isère, et la restaurer poserait un départ hors du rayon. */
export function memoriser(ville, commune) {
  try {
    localStorage.setItem(CLE, JSON.stringify({ ville, commune }));
  } catch {
    /* stockage interdit ou saturé : le départ reste valable pour cette visite */
  }
}

export function restaurer(ville) {
  try {
    const d = JSON.parse(localStorage.getItem(CLE) || 'null');
    return d && d.ville === ville && d.commune ? d.commune : null;
  } catch {
    // Stockage refusé, ou contenu abîmé : on repart de zéro plutôt que de casser
    // la page. Le point de départ est un agrément, jamais un prérequis.
    return null;
  }
}

export function oublier() {
  try {
    localStorage.removeItem(CLE);
  } catch { /* rien à faire */ }
}
