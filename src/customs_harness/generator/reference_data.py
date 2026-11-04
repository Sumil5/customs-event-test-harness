"""Reference data for realistic synthetic declarations. Illustrative only."""

from __future__ import annotations

# (commodity code, description). Chapters 01-04 are "controlled" for risk scoring.
COMMODITIES: list[tuple[str, str]] = [
    ("84713000", "Portable laptop computers, 14-inch"),
    ("85171300", "Smartphones"),
    ("61091000", "Cotton T-shirts, assorted sizes"),
    ("94036010", "Wooden dining tables"),
    ("87120030", "Bicycles with ball bearings"),
    ("30049000", "Medicaments packaged for retail sale"),
    ("39269097", "Plastic storage boxes"),
    ("73181595", "Steel hexagon bolts"),
    ("08081080", "Fresh apples"),
    ("19053199", "Sweet biscuits"),
    ("48191000", "Corrugated cardboard cartons"),
    ("64039993", "Leather footwear"),
    ("21069098", "Food preparations, flavoured syrups"),
    ("95030075", "Plastic toys"),
]
CONTROLLED_COMMODITIES: list[tuple[str, str]] = [
    ("02013000", "Fresh boneless beef"),
    ("02071410", "Frozen chicken cuts"),
    ("03044100", "Atlantic salmon fillets, fresh"),
    ("04090000", "Natural honey"),
    ("04069021", "Cheddar cheese"),
    ("01022910", "Live cattle for breeding"),
]
CONTROLLED_CHAPTERS = frozenset({"01", "02", "03", "04"})

GB_TOWNS = ["London", "Manchester", "Birmingham", "Glasgow", "Cardiff", "Liverpool", "Leeds", "Bristol"]
NI_TOWNS = ["Belfast", "Newry", "Lisburn", "Armagh", "Enniskillen", "Ballymena", "Londonderry"]
EU_TOWNS = {
    "IE": ["Dublin", "Cork", "Galway", "Dundalk"],
    "FR": ["Rouen", "Lyon", "Lille"],
    "DE": ["Hamburg", "Köln", "München"],
    "NL": ["Rotterdam", "Utrecht"],
}
FAKER_LOCALE = {"GB": "en_GB", "IE": "en_IE", "FR": "fr_FR", "DE": "de_DE", "NL": "nl_NL"}

NI_PORTS = ["GBBEL", "GBLAR", "GBWPT"]  # Belfast, Larne, Warrenpoint
GB_PORTS = ["GBLIV", "GBHLY"]  # Liverpool, Holyhead
EU_PORTS = ["IEDUB", "NLRTM"]  # Dublin, Rotterdam

# Names with diacritics: exercise UTF-8 handling end to end.
UNICODE_TRADERS = [
    "Ó Briain Agri Teoranta",
    "Ní Bhriain Seafoods Teo",
    "Bwydydd Môn Cyf",
    "Fromagerie Dubé SARL",
    "Müller Logistik GmbH",
    "Łódź Meble Sp. z o.o.",
    "Øresund Freight ApS",
    "Café Crème Distributors Ltd",
]
UNICODE_DESCRIPTIONS = [
    "Crème fraîche, 30% fat - pots de 500 g",
    "Caws Cymreig aeddfed (Welsh mature cheese) - 2.5 kg",
    "Käse-Spätzle, tiefgekühlt",
    "Smoked salmon - « qualité supérieure »",
    "Jamón ibérico, sliced - 100 g packs",
]
