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


def demo_events(sector_id: str = "villemoirieu") -> list[Event]:
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
        ev("Marché des producteurs", today, 8, "Villemoirieu", "marche",
           "Producteurs du nord-Isère et de l'Ain : fromages, miels, fruits de saison. Entrée libre.",
           free=True, audience=["tous"], end=_at(today, 13)),
        ev("Visite guidée des halles médiévales de Crémieu", today, 19, "Crémieu", "patrimoine",
           "Découverte au crépuscule des halles du XVe siècle et des remparts avec un guide du patrimoine. Balade dans la cité médiévale.",
           free=False, price_text="12 €", audience=["tous"]),
        ev("Ciné plein air : films sous les étoiles", today + timedelta(days=1), 21, "L'Isle-d'Abeau", "cinema",
           "Projection gratuite en plein air au parc Saint-Hubert. Amenez plaids et chaises pliantes !",
           free=True, audience=["famille", "tous"]),
        ev("Atelier poterie parents-enfants", today + timedelta(days=1), 10, "Bourgoin-Jallieu", "atelier",
           "Initiation au modelage pour les enfants dès 5 ans accompagnés d'un parent. Matériel fourni, cuisson comprise.",
           free=False, price_text="15 € par duo", audience=["famille", "enfants"]),

        # ---- Ce week-end ------------------------------------------------------------
        ev("Fête médiévale de Crémieu", sat, 10, "Crémieu", "fete",
           "La cité médiévale en fête : campements, tournois de chevalerie, marché artisanal et banquet. Animations pour toute la famille.",
           free=True, audience=["famille", "tous"], end=_at(sun, 19)),
        ev("Base de loisirs de la Vallée Bleue : voile et baignade", sat, 9, "Montalieu-Vercieu", "nature",
           "Découverte de la voile et du paddle sur le plan d'eau, plage surveillée et sentiers au bord du Rhône. Pour toute la famille.",
           free=False, price_text="8 €", audience=["famille"]),
        ev("Concert : jazz manouche au kiosque", sat, 20, "Pérouges", "concert",
           "Un quartet revisite Django Reinhardt au pied de la cité médiévale. Buvette sur place.",
           free=True, audience=["tous"]),
        ev("Tournoi de beach-volley open", sat, 9, "Meyzieu", "sport",
           "Tournoi ouvert à tous niveaux, par équipes de 2, à la base de loisirs. Inscription sur place dès 8h30.",
           free=False, price_text="10 € / équipe", audience=["adultes", "ados"]),
        ev("Spectacle de marionnettes : Le voyage de Plume", sun, 16, "Vienne", "jeunesse",
           "Théâtre d'ombres et marionnettes pour les 3-8 ans, par la compagnie du Chat Perché. Durée 45 min.",
           free=False, price_text="6 €", audience=["enfants", "famille"]),
        ev("Brocante et vide-grenier du centre-ville", sun, 7, "Bourgoin-Jallieu", "marche",
           "Plus de 200 exposants dans les rues du centre. Chinez meubles, livres et jouets toute la journée.",
           free=True, audience=["tous"], end=_at(sun, 18)),
        ev("Balade contée en famille au bord du Rhône", sun, 10, "Loyettes", "jeunesse",
           "Une conteuse vous emmène sur les traces des légendes du fleuve et des lônes. Dès 4 ans.",
           free=False, price_text="5 €, gratuit -6 ans", audience=["famille", "enfants"]),

        # ---- Semaine prochaine ------------------------------------------------------
        ev("Exposition : Morestel, cité des peintres", sat + timedelta(days=3), 10, "Morestel", "expo",
           "40 tirages et toiles autour de la lumière du Dauphiné à la maison Ravier. Un siècle de peinture de plein air.",
           free=True, audience=["tous"], end=_at(sat + timedelta(days=45), 18)),
        ev("Conférence : le Rhône, un fleuve et ses métiers", sat + timedelta(days=4), 18, "Lagnieu", "conference",
           "Un historien retrace la batellerie et les crues du Rhône. Échange avec le public.",
           free=True, audience=["adultes"]),
        ev("Marché nocturne des artisans", sat + timedelta(days=5), 18, "La Tour-du-Pin", "marche",
           "Créateurs et producteurs des Vals du Dauphiné, animations musicales et restauration en terrasse jusqu'à minuit.",
           free=True, audience=["tous"]),
        ev("Stage d'escalade découverte ados", sat + timedelta(days=5), 14, "Saint-Chef", "sport",
           "Après-midi d'initiation en falaise-école encadrée par un guide. Matériel fourni. 12-17 ans.",
           free=False, price_text="25 €", audience=["ados"]),
        ev("Festival de musique : ouverture symphonique", sat + timedelta(days=7), 21, "Lyon", "festival",
           "Concert d'ouverture à l'auditorium : la Symphonie fantastique par l'Orchestre national de Lyon.",
           free=False, price_text="de 15 à 45 €", audience=["tous"]),
        ev("Atelier réparation vélo participatif", sat + timedelta(days=6), 9, "Villefontaine", "atelier",
           "Apprenez à régler freins et dérailleurs avec les bénévoles de l'atelier. Outils et conseils gratuits.",
           free=True, audience=["tous"]),
        ev("Visite du château de Saint-Vulbas et du parc", sat + timedelta(days=6), 14, "Saint-Vulbas", "patrimoine",
           "Visite guidée du patrimoine de la plaine de l'Ain, entre nature et histoire industrielle.",
           free=True, audience=["famille", "tous"]),
        ev("Trail des étangs de la Dombes", sat + timedelta(days=8), 8, "Meximieux", "sport",
           "Courses de 12 à 42 km entre étangs et bois, au départ des portes de la Dombes.",
           free=False, price_text="dès 22 €", audience=["adultes"]),
        ev("Soirée astronomie au parc de Miribel-Jonage", sat + timedelta(days=9), 21, "Jonage", "nature",
           "Observation des étoiles avec les télescopes du club d'astronomie. Prévoir vêtements chauds.",
           free=True, audience=["famille", "tous"]),

        # ---- Plus tard dans le mois ------------------------------------------------
        ev("Théâtre antique : Cyrano sous les étoiles", sat + timedelta(days=11), 20, "Vienne", "spectacle",
           "Représentation exceptionnelle au théâtre antique : Cyrano de Bergerac en plein air.",
           free=False, price_text="de 12 à 35 €", audience=["tous"]),
        ev("Fête du lac et feu d'artifice", sat + timedelta(days=13), 19, "Les Avenières", "fete",
           "Bal populaire, guinguette au bord de l'eau et grand feu d'artifice à la tombée de la nuit.",
           free=True, audience=["famille", "tous"]),
        ev("Salon du livre jeunesse", sat + timedelta(days=14), 10, "Ambérieu-en-Bugey", "jeunesse",
           "Rencontres avec 30 auteurs et illustrateurs, ateliers BD et lectures animées pour les 3-14 ans.",
           free=True, audience=["enfants", "famille"]),
        ev("Concert symphonique : Mozart à la collégiale", sat + timedelta(days=15), 20, "Crémieu", "concert",
           "L'orchestre de chambre joue les symphonies 40 et 41 dans l'acoustique de la collégiale Saint-Jean.",
           free=False, price_text="18 €, réduit 12 €", audience=["adultes", "seniors"]),
        ev("Initiation à la pêche en rivière", sat + timedelta(days=16), 9, "Chavanoz", "nature",
           "La fédération de pêche fait découvrir la pêche au coup aux enfants dès 7 ans, au bord de la Bourbre. Matériel prêté.",
           free=True, audience=["enfants", "famille"]),
        ev("Marché paysan de la plaine de l'Ain", sat + timedelta(days=20), 8, "Pérouges", "marche",
           "Le grand marché mensuel des producteurs : viandes, fromages et légumes de la Dombes et du Bugey.",
           free=True, audience=["tous"]),
        ev("Rando-dégustation au bord du Rhône", sat + timedelta(days=21), 9, "Montalieu-Vercieu", "nature",
           "Randonnée de 3h ponctuée de haltes gourmandes chez les producteurs du val de Rhône.",
           free=False, price_text="20 €", audience=["adultes", "famille"]),
        ev("Nocturne au musée : arts et lumières", sat + timedelta(days=23), 19, "Lyon", "expo",
           "Le musée des Beaux-Arts ouvre ses portes en soirée : parcours sonore autour des collections.",
           free=True, audience=["tous"]),
    ]
