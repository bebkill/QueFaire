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

const animationsReduites = () =>
  globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;

/**
 * Crée la carte et rend de quoi la piloter.
 *
 * `onSelection` est le lien carte → liste : la page y branche la mise en
 * évidence de ses tuiles. On lui passe un tableau vide quand plus rien n'est
 * sélectionné (bulle refermée).
 */
export async function creerCarte({ el, centre, onSelection }) {
  const L = (await import('leaflet')).default;
  await import('leaflet/dist/leaflet.css');

  const map = L.map(el, { scrollWheelZoom: false }).setView([centre.lat, centre.lon], 9);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 18,
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);

  const couche = L.layerGroup().addTo(map);
  const coucheOrigine = L.layerGroup().addTo(map);

  let marqueurs = new Map();   // entrée → marqueur (les groupés pointent le leur)
  let derniers = [];           // items du dernier `afficher`, pour redessiner au zoom
  let origine = null;
  let survole = null;
  let enGlissement = false;
  let fermetureTimer = null;

  // Le zoom change la granularité des groupes — sans recadrer la vue.
  map.on('zoomend', () => dessiner(derniers, false));
  map.on('dragstart', () => { enGlissement = true; });
  map.on('dragend', () => { enGlissement = false; });
  // Bulle fermée = plus rien de sélectionné. Le délai laisse le clic sur une
  // AUTRE bulle (qui ferme celle-ci pour ouvrir la sienne) annuler l'effacement.
  map.on('popupclose', () => {
    clearTimeout(fermetureTimer);
    fermetureTimer = setTimeout(() => onSelection([]), 80);
  });

  const choisir = (entrees) => {
    clearTimeout(fermetureTimer);
    onSelection(entrees);
  };

  function ajouterPoint(it) {
    const m = it.isPlace
      // Activité permanente : pastille à icône, nettement distincte du point
      // coloré d'un événement.
      ? L.marker([it.lat, it.lon], {
          icon: L.divIcon({
            className: 'place-icon',
            html: `<div class="place-dot${it.unusual ? ' is-unusual' : ''}">${it.emoji}</div>`,
            iconSize: [30, 30],
            iconAnchor: [15, 15],
          }),
        })
      : L.circleMarker([it.lat, it.lon], {
          radius: 8, color: it.color, fillColor: it.color, fillOpacity: 0.75, weight: 2,
        });
    // Survol : aperçu de la fiche. Clic : on remonte jusqu'à sa tuile dans la
    // liste (mise en évidence + défilement), sans bulle.
    m.bindTooltip(it.tooltip, {
      direction: 'top', offset: [0, -6], opacity: 1, className: 'event-tip',
    })
      .on('click', () => choisir([it.entree]))
      .addTo(couche);
    return m;
  }

  function ouvrirGroupe(groupe) {
    const cadre = L.latLngBounds(groupe.items.map((i) => [i.lat, i.lon]));
    const etendue = cadre.getNorthEast().distanceTo(cadre.getSouthWest());
    if (etendue < 100 || map.getZoom() >= 15) {
      // Tout au même endroit (géocodage à la commune) : liste plutôt que zoom,
      // et mise en évidence de toutes les fiches du groupe dans la grille.
      const liens = groupe.items.slice(0, 12)
        .map((i) => `<a href="${i.href}">${i.title}</a>`).join('<br>');
      const reste = groupe.items.length > 12
        ? `<br>… et ${groupe.items.length - 12} autres` : '';
      L.popup({ maxHeight: 220 })
        .setLatLng([groupe.lat, groupe.lon])
        .setContent(liens + reste)
        .openOn(map);
      choisir(groupe.items.map((i) => i.entree));
    } else {
      map.fitBounds(cadre, { padding: [50, 50] });
    }
  }

  function ajouterGroupe(groupe) {
    const total = groupe.items.length;
    const taille = Math.max(38, Math.min(64, 32 + total * 1.2));
    const quePermanent = groupe.items.every((i) => i.isPlace);
    const quoi = quePermanent ? 'activités' : 'fiches';
    return L.marker([groupe.lat, groupe.lon], {
      icon: L.divIcon({
        className: `cluster-icon${quePermanent ? ' cluster-places' : ''}`,
        html: `<div class="cluster-dot" style="width:${taille}px;height:${taille}px" title="${total} ${quoi} — cliquer pour zoomer">${total}</div>`,
        iconSize: [taille, taille],
        iconAnchor: [taille / 2, taille / 2],
      }),
    }).on('click', () => ouvrirGroupe(groupe)).addTo(couche);
  }

  function dessiner(items, recadrer) {
    couche.clearLayers();
    marqueurs = new Map();
    survole = null;
    const points = [];
    const projeter = (lat, lon) => map.project([lat, lon], map.getZoom());
    for (const groupe of grouperPoints(items, projeter)) {
      if (groupe.items.length >= 2) {
        const m = ajouterGroupe(groupe);
        // Toutes les entrées d'un groupe désignent le marqueur du groupe :
        // survoler une tuile prise dans un amas met l'amas en évidence, faute
        // de pouvoir désigner son point propre.
        for (const it of groupe.items) marqueurs.set(it.entree, m);
        points.push([groupe.lat, groupe.lon]);
      } else {
        const it = groupe.items[0];
        marqueurs.set(it.entree, ajouterPoint(it));
        points.push([it.lat, it.lon]);
      }
    }
    if (!recadrer) return;
    if (!points.length) {
      if (origine) map.setView([origine.lat, origine.lon], 12);
      return;
    }
    const cadre = L.latLngBounds(points);
    // L'origine n'entre dans le cadrage que si elle est DANS le nuage, à une
    // marge près : consulter le nord-Isère depuis Paris ne doit pas donner une
    // carte de France centrée sur le vide. Son marqueur est posé quand même,
    // simplement hors cadre initial.
    if (origine && cadre.pad(0.5).contains([origine.lat, origine.lon])) {
      cadre.extend([origine.lat, origine.lon]);
    }
    map.fitBounds(cadre, { padding: [40, 40], maxZoom: 13 });
  }

  function mettreEnAvant(m, actif) {
    if (!m) return;
    if (m.setStyle) {
      m.setStyle(actif ? { radius: 12, weight: 4 } : { radius: 8, weight: 2 });
    } else {
      m.getElement()?.classList.toggle('is-hover', actif);
    }
    if (actif && m.bringToFront) m.bringToFront();
  }

  return {
    afficher(items, { recadrer = false } = {}) {
      derniers = items;
      dessiner(items, recadrer);
    },

    /** `null` retire le marqueur. Une origine de source `defaut` (centre du
     *  secteur) ne doit JAMAIS arriver ici : la page passe `null` à sa place. */
    poserOrigine(o) {
      origine = o;
      coucheOrigine.clearLayers();
      if (!o) return;
      if (o.source === 'geoloc') {
        // Le cercle de précision dit lui-même ce que le point vaut. En deçà de
        // 50 m il serait plus petit que la pastille : inutile de le dessiner.
        if (o.precision && o.precision > 50) {
          L.circle([o.lat, o.lon], {
            radius: o.precision, color: '#1c7ed6', weight: 1,
            fillColor: '#1c7ed6', fillOpacity: 0.1, className: 'origine-halo',
          }).addTo(coucheOrigine);
        }
        L.circleMarker([o.lat, o.lon], {
          radius: 7, color: '#ffffff', weight: 3,
          fillColor: '#1c7ed6', fillOpacity: 1, className: 'origine-point',
        }).bindPopup('Vous êtes ici').addTo(coucheOrigine);
      } else {
        // Repère VISUELLEMENT DISTINCT du point GPS : 15 m de précision et
        // 2 km de centre-bourg ne se lisent pas pareil sur une carte.
        L.marker([o.lat, o.lon], {
          icon: L.divIcon({
            className: 'origine-icon',
            html: '<div class="origine-maison">🏠</div>',
            iconSize: [30, 30], iconAnchor: [15, 15],
          }),
        }).bindPopup(`Départ : ${o.libelle}<br><small>centre de la commune</small>`)
          .addTo(coucheOrigine);
      }
    },

    centrerSurOrigine() {
      if (!origine) return;
      map.setView([origine.lat, origine.lon], Math.max(map.getZoom(), 12), {
        animate: !animationsReduites(),
      });
    },

    /** Met en évidence le point d'une entrée, et l'amène dans le cadre s'il en
     *  est sorti. `null` relâche la mise en évidence. */
    survoler(entree) {
      const cible = entree ? marqueurs.get(entree) || null : null;
      if (cible === survole) return;
      mettreEnAvant(survole, false);
      survole = cible;
      if (!cible) return;
      mettreEnAvant(cible, true);
      const ou = cible.getLatLng();
      // Pas de recadrage pendant que le visiteur fait glisser la carte : elle
      // lui échapperait des mains.
      if (!enGlissement && !map.getBounds().pad(-0.15).contains(ou)) {
        map.panTo(ou, { animate: !animationsReduites() });
      }
    },

    redimensionner() {
      map.invalidateSize();
    },
  };
}
