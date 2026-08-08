/** La carte, et rien d'autre.
 *
 * Ce module ne connaît ni secteur, ni catégorie, ni filtre : il reçoit des
 * points DÉJÀ CUITS — titre, couleur, émoji, infobulle en HTML — et se charge de
 * les placer, de les grouper quand ils se marchent dessus, et de mettre en
 * évidence celui qu'on lui désigne. Tout ce qui relève du vocabulaire du
 * catalogue (libellés de catégorie, horaires, distinctions) reste dans la page,
 * qui est la seule à le connaître.
 *
 * Leaflet est importé DYNAMIQUEMENT : la carte est un mode de vue qu'on active,
 * et la majorité des visites ne l'ouvrent jamais.
 */

/**
 * Regroupe les points qui se marchent dessus à l'écran.
 *
 * `projeter` rend des pixels au zoom courant : c'est ce qui rend la fonction
 * pure, et donc testable. Le groupement se fait en distance ÉCRAN et non en
 * distance terrain — deux musées à 300 m se chevauchent au zoom 9 et pas au
 * zoom 15, et c'est bien le chevauchement qu'on veut résoudre.
 *
 * Un point rejoint le groupe le PLUS PROCHE dans le rayon, jamais le premier
 * rencontré : sinon l'ordre du tableau déciderait, et un point pourrait sauter
 * sur un amas plus lointain que son voisin immédiat.
 */
export function grouperPoints(items, projeter, rayon = 70) {
  const groupes = [];
  for (const it of items) {
    const pt = projeter(it.lat, it.lon);
    let meilleur = null;
    let meilleureD = Infinity;
    for (const g of groupes) {
      const d = Math.hypot(pt.x - g.pt.x, pt.y - g.pt.y);
      if (d < rayon && d < meilleureD) { meilleur = g; meilleureD = d; }
    }
    if (meilleur) {
      meilleur.items.push(it);
      // Centroïde glissant : le groupe se recentre à chaque recrue, sinon il
      // resterait accroché à son premier point et dériverait du nuage réel.
      meilleur.lat += (it.lat - meilleur.lat) / meilleur.items.length;
      meilleur.lon += (it.lon - meilleur.lon) / meilleur.items.length;
      meilleur.pt = projeter(meilleur.lat, meilleur.lon);
    } else {
      groupes.push({ lat: it.lat, lon: it.lon, pt, items: [it] });
    }
  }
  return groupes;
}
