"""Jeu de données de démonstration.

Sert à développer et à faire tourner le site tant que le crawl réel n'est pas
branché (clés API, CI). Les dates sont relatives au jour du build pour que la
démo reste vivante : « ce week-end » tombe toujours le vrai prochain week-end.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .models import Event


def _next_saturday(today: date) -> date:
    return today + timedelta(days=(5 - today.weekday()) % 7)


def _at(day: date, hour: int, minute: int = 0) -> str:
    return datetime.combine(day, time(hour, minute)).isoformat()


def demo_events(sector_id: str = "isere") -> list[Event]:
    today = date.today()
    sat = _next_saturday(today)
    sun = sat + timedelta(days=1)

    def ev(title, day, hour, commune, category, desc, **kw):
        return Event(
            title=title,
            start=_at(day, hour),
            commune=commune,
            category=category,
            description=desc,
            source_id=kw.pop("source_id", "demo"),
            sector=sector_id,
            **kw,
        )

    return [
        # ---- Aujourd'hui / demain -------------------------------------------------
        ev("Marché des producteurs", today, 8, "Grenoble", "marche",
           "Producteurs locaux du Vercors et de la Chartreuse : fromages, miels, fruits de saison. Entrée libre.",
           free=True, audience=["tous"], end=_at(today, 13)),
        ev("Visite guidée de la Bastille au crépuscule", today, 19, "Grenoble", "patrimoine",
           "Montée en téléphérique et découverte des fortifications avec un guide du patrimoine. Vue panoramique sur les trois massifs.",
           free=False, price_text="12 €", audience=["tous"]),
        ev("Ciné plein air : films sous les étoiles", today + timedelta(days=1), 21, "Échirolles", "cinema",
           "Projection gratuite en plein air au parc Maurice-Thorez. Amenez plaids et chaises pliantes !",
           free=True, audience=["famille", "tous"]),
        ev("Atelier poterie parents-enfants", today + timedelta(days=1), 10, "Meylan", "atelier",
           "Initiation au modelage pour les enfants dès 5 ans accompagnés d'un parent. Matériel fourni, cuisson comprise.",
           free=False, price_text="15 € par duo", audience=["famille", "enfants"]),

        # ---- Ce week-end ------------------------------------------------------------
        ev("Fête médiévale de Crémieu", sat, 10, "Crémieu", "fete",
           "La cité médiévale en fête : campements, tournois de chevalerie, marché artisanal et banquet. Animations pour toute la famille.",
           free=True, audience=["famille", "tous"], end=_at(sun, 19)),
        ev("Randonnée accompagnée au lac Achard", sat, 9, "Chamrousse", "nature",
           "Boucle familiale de 2h30 vers l'un des plus beaux lacs de Belledonne, avec un accompagnateur moyenne montagne. Dès 8 ans.",
           free=False, price_text="8 €", audience=["famille"]),
        ev("Concert : jazz manouche au kiosque", sat, 20, "Voiron", "concert",
           "Le quartet Swing 38 revisite Django Reinhardt sous le kiosque du jardin de ville. Buvette sur place.",
           free=True, audience=["tous"]),
        ev("Tournoi de beach-volley open", sat, 9, "Saint-Égrève", "sport",
           "Tournoi ouvert à tous niveaux, par équipes de 2. Inscription sur place dès 8h30.",
           free=False, price_text="10 € / équipe", audience=["adultes", "ados"]),
        ev("Spectacle de marionnettes : Le voyage de Plume", sun, 16, "Vienne", "jeunesse",
           "Théâtre d'ombres et marionnettes pour les 3-8 ans, par la compagnie du Chat Perché. Durée 45 min.",
           free=False, price_text="6 €", audience=["enfants", "famille"]),
        ev("Brocante et vide-grenier du centre-ville", sun, 7, "Bourgoin-Jallieu", "marche",
           "Plus de 200 exposants dans les rues du centre. Chinez meubles, livres et jouets toute la journée.",
           free=True, audience=["tous"], end=_at(sun, 18)),
        ev("Balade contée en famille dans les gorges", sun, 10, "Sassenage", "jeunesse",
           "Une conteuse vous emmène sur les traces des légendes des Cuves de Sassenage. Dès 4 ans.",
           free=False, price_text="5 €, gratuit -6 ans", audience=["famille", "enfants"]),

        # ---- Semaine prochaine ------------------------------------------------------
        ev("Exposition : Trésors des Alpes, photographies", sat + timedelta(days=3), 10, "Grenoble", "expo",
           "40 tirages grand format sur la faune alpine au Muséum. Bouquetins, gypaètes et lagopèdes comme vous ne les avez jamais vus.",
           free=True, audience=["tous"], end=_at(sat + timedelta(days=45), 18)),
        ev("Conférence : les glaciers de l'Oisans face au climat", sat + timedelta(days=4), 18, "Le Bourg-d'Oisans", "conference",
           "Un glaciologue du CNRS présente 40 ans de mesures sur les glaciers des Écrins. Échange avec le public.",
           free=True, audience=["adultes"]),
        ev("Marché nocturne des artisans", sat + timedelta(days=5), 18, "Villard-de-Lans", "marche",
           "Créateurs et producteurs du Vercors, animations musicales et restauration en terrasse jusqu'à minuit.",
           free=True, audience=["tous"]),
        ev("Stage d'escalade découverte ados", sat + timedelta(days=5), 14, "Le Bourg-d'Oisans", "sport",
           "Après-midi d'initiation en falaise-école encadrée par un guide. Matériel fourni. 12-17 ans.",
           free=False, price_text="25 €", audience=["ados"]),
        ev("Festival Berlioz : ouverture symphonique", sat + timedelta(days=7), 21, "La Côte-Saint-André", "festival",
           "Concert d'ouverture dans la cour du château Louis XI : la Symphonie fantastique par l'Orchestre national de Lyon.",
           free=False, price_text="de 15 à 45 €", audience=["tous"]),
        ev("Atelier réparation vélo participatif", sat + timedelta(days=6), 9, "Fontaine", "atelier",
           "Apprenez à régler freins et dérailleurs avec les bénévoles de l'atelier. Outils et conseils gratuits.",
           free=True, audience=["tous"]),
        ev("Visite du château de Vizille et son parc", sat + timedelta(days=6), 14, "Vizille", "patrimoine",
           "Visite guidée du berceau de la Révolution française et de son domaine, entre roseraie et hérons cendrés.",
           free=True, audience=["famille", "tous"]),
        ev("Trail des Passerelles du Monteynard", sat + timedelta(days=8), 8, "Monestier-de-Clermont", "sport",
           "Courses de 15 à 65 km autour du lac turquoise du Monteynard et ses passerelles himalayennes suspendues.",
           free=False, price_text="dès 22 €", audience=["adultes"]),
        ev("Soirée astronomie au col de Porte", sat + timedelta(days=9), 21, "Saint-Laurent-du-Pont", "nature",
           "Observation des étoiles avec les télescopes du club d'astronomie de Chartreuse. Prévoir vêtements chauds.",
           free=True, audience=["famille", "tous"]),

        # ---- Plus tard dans le mois ------------------------------------------------
        ev("Les Nuits de Fourvière hors les murs : théâtre", sat + timedelta(days=11), 20, "Vienne", "spectacle",
           "Représentation exceptionnelle au théâtre antique : Cyrano de Bergerac sous les étoiles.",
           free=False, price_text="de 12 à 38 €", audience=["tous"]),
        ev("Fête du lac et feu d'artifice", sat + timedelta(days=13), 19, "Morestel", "fete",
           "Bal populaire, guinguette au bord de l'eau et grand feu d'artifice à la tombée de la nuit.",
           free=True, audience=["famille", "tous"]),
        ev("Salon du livre jeunesse", sat + timedelta(days=14), 10, "Échirolles", "jeunesse",
           "Rencontres avec 30 auteurs et illustrateurs, ateliers BD et lectures animées pour les 3-14 ans.",
           free=True, audience=["enfants", "famille"]),
        ev("Concert symphonique : Mozart au couvent", sat + timedelta(days=15), 20, "Voreppe", "concert",
           "L'orchestre de chambre Alpes-Dauphiné joue les symphonies 40 et 41 dans l'acoustique du couvent des Carmes.",
           free=False, price_text="18 €, réduit 12 €", audience=["adultes", "seniors"]),
        ev("Initiation à la pêche en rivière", sat + timedelta(days=16), 9, "Pontcharra", "nature",
           "La fédération de pêche de l'Isère fait découvrir la pêche au coup aux enfants dès 7 ans. Matériel prêté.",
           free=True, audience=["enfants", "famille"]),
        ev("Marché paysan de la Matheysine", sat + timedelta(days=20), 8, "La Mure", "marche",
           "Le grand marché mensuel des producteurs du plateau : viandes, fromages de la Mure et légumes de montagne.",
           free=True, audience=["tous"]),
        ev("Rando-dégustation en Chartreuse", sat + timedelta(days=21), 9, "Saint-Laurent-du-Pont", "nature",
           "Randonnée de 3h ponctuée de haltes gourmandes chez les producteurs du massif de Chartreuse.",
           free=False, price_text="20 €", audience=["adultes", "famille"]),
        ev("Nocturne du musée : arts et lumières", sat + timedelta(days=23), 19, "Grenoble", "expo",
           "Le musée de Grenoble ouvre ses portes en soirée : parcours sonore et médiation autour des collections modernes.",
           free=True, audience=["tous"]),
    ]
