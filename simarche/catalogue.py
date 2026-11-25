"""Catalogue : besoins des foyers et produits du grossiste (marques fictives)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Need:
    id: str
    nom: str
    conso: float          # portions par adulte et par jour
    facteur_enfant: float  # multiplicateur pour un enfant de 3 à 17 ans
    stock_max_jours: float
    essentiel: bool
    heterogene: bool = False  # produits complémentaires plutôt que substituables


@dataclass(frozen=True)
class Product:
    id: str
    nom: str
    besoin: str
    rayon: int
    gamme: str            # mdd | marque | premium | bio
    prix_conseille: float
    prix_achat: float
    portions: float
    conservation_j: int
    enfant: float          # attrait pour les enfants (0-1)
    qualite: float         # qualité intrinsèque cachée (0-1)
    bio: bool = False
    local: bool = False
    impulsion: bool = False
    alcool: bool = False
    porc: bool = False
    viande: bool = False
    poisson: bool = False
    gluten: bool = False
    animal: str = ""      # chat | chien
    frais: bool = False

    @property
    def alimentaire(self) -> bool:
        return self.besoin not in ("hygiene", "entretien", "animaux") and self.id != "couches"


NEEDS: dict[str, Need] = {n.id: n for n in [
    Need("pain", "pain", 1.0, 0.8, 3, True),
    Need("lait", "lait", 0.25, 1.6, 12, True),
    Need("laitiers", "produits laitiers (yaourts, fromage, beurre)", 0.9, 1.2, 10, False, True),
    Need("oeufs", "œufs", 0.35, 0.8, 14, True),
    Need("proteines", "viande, poisson ou protéines", 1.0, 0.7, 5, True, True),
    Need("feculents", "féculents (pâtes, riz, pommes de terre)", 1.0, 0.8, 30, True, True),
    Need("fruits", "fruits", 1.3, 1.1, 7, True, True),
    Need("legumes", "légumes", 1.3, 0.7, 7, True, True),
    Need("epicerie", "épicerie (sauces, huile)", 0.4, 0.6, 40, False, True),
    Need("petit_dej", "petit-déjeuner (céréales, café, confiture)", 1.0, 1.0, 21, True, True),
    Need("snacks", "goûters et douceurs", 0.4, 1.6, 14, False, True),
    Need("boissons", "boissons", 1.0, 0.8, 14, True, True),
    Need("hygiene", "hygiène (papier toilette, gel douche, dentifrice)", 0.3, 0.8, 30, True, True),
    Need("entretien", "entretien (lessive, vaisselle)", 0.3, 0.3, 40, False, True),
    Need("bebe", "bébé (couches, petits pots)", 0.0, 0.0, 14, True, True),
    Need("animaux", "nourriture pour animal", 0.0, 0.0, 21, True),
    Need("plats", "plats préparés et surgelés", 0.3, 0.6, 7, False),
]}

MARGE_BESOIN = {
    "pain": 0.35, "fruits": 0.30, "legumes": 0.30, "lait": 0.22, "laitiers": 0.25, "oeufs": 0.24,
    "proteines": 0.24, "feculents": 0.28, "epicerie": 0.28, "petit_dej": 0.30, "snacks": 0.32,
    "boissons": 0.30, "hygiene": 0.33, "entretien": 0.33, "bebe": 0.20, "animaux": 0.28, "plats": 0.30,
}

RAYONS = {
    0: "Fruits, légumes et pain",
    1: "Frais (lait, laitiers, œufs, viande)",
    2: "Épicerie salée",
    3: "Petit-déjeuner et sucré",
    4: "Boissons et surgelés",
    5: "Hygiène, entretien, bébé, animaux",
}

NIVEAUX = ["bas", "mains", "yeux", "haut"]
EMPLACEMENTS_SECONDAIRES = {"entree": 2, "tete_gondole_1": 2, "tete_gondole_2": 2, "caisse": 3}


def _p(id, nom, besoin, rayon, gamme, prix, portions, conservation, enfant, qualite, **kw) -> Product:
    marge = MARGE_BESOIN[besoin]
    return Product(id=id, nom=nom, besoin=besoin, rayon=rayon, gamme=gamme, prix_conseille=prix,
                   prix_achat=round(prix * (1 - marge), 2), portions=portions, conservation_j=conservation,
                   enfant=enfant, qualite=qualite, **kw)


PRODUCTS: dict[str, Product] = {p.id: p for p in [
    # Rayon 0 : fruits, légumes, pain
    _p("baguette", "Baguette tradition", "pain", 0, "marque", 1.15, 2, 1, 0.3, 0.80, gluten=True, frais=True),
    _p("pain_mie_mdd", "Pain de mie (premier prix)", "pain", 0, "mdd", 1.45, 6, 7, 0.4, 0.45, gluten=True),
    _p("pain_complet_bio", "Pain complet bio Ferme des Saules", "pain", 0, "bio", 2.90, 4, 3, 0.1, 0.85, bio=True, gluten=True, frais=True),
    _p("pommes", "Pommes Gala 1 kg (producteur local)", "fruits", 0, "marque", 2.40, 6, 14, 0.5, 0.70, local=True, frais=True),
    _p("bananes", "Bananes 1 kg", "fruits", 0, "mdd", 1.85, 6, 6, 0.6, 0.60, frais=True),
    _p("oranges_bio", "Oranges bio 1,5 kg", "fruits", 0, "bio", 4.50, 8, 14, 0.4, 0.80, bio=True, frais=True),
    _p("carottes", "Carottes 1 kg (producteur local)", "legumes", 0, "mdd", 1.35, 6, 14, 0.2, 0.65, local=True, frais=True),
    _p("tomates", "Tomates grappe 1 kg", "legumes", 0, "marque", 3.20, 6, 6, 0.2, 0.60, frais=True),
    _p("salade", "Salade batavia", "legumes", 0, "mdd", 1.15, 3, 4, 0.0, 0.70, local=True, frais=True),
    _p("pdt", "Pommes de terre 2,5 kg", "feculents", 0, "mdd", 3.30, 12, 30, 0.3, 0.65, local=True),
    # Rayon 1 : frais
    _p("lait_mdd", "Lait demi-écrémé 1 L (premier prix)", "lait", 1, "mdd", 0.99, 1, 60, 0.3, 0.50),
    _p("lait_marque", "Lait demi-écrémé 1 L Vallée Verte", "lait", 1, "marque", 1.29, 1, 60, 0.4, 0.70),
    _p("lait_bio", "Lait bio 1 L Ferme des Saules", "lait", 1, "bio", 1.69, 1, 60, 0.3, 0.85, bio=True, local=True),
    _p("yaourts_mdd", "Yaourts nature x8 (premier prix)", "laitiers", 1, "mdd", 1.65, 8, 20, 0.4, 0.50, frais=True),
    _p("yaourts_marque", "Yaourts aux fruits x8 Petit Matin", "laitiers", 1, "marque", 2.95, 8, 20, 0.85, 0.70, frais=True),
    _p("fromage_rape", "Emmental râpé 200 g", "laitiers", 1, "mdd", 1.99, 5, 25, 0.5, 0.55, frais=True),
    _p("camembert", "Camembert de Normandie AOP", "laitiers", 1, "premium", 2.95, 5, 18, 0.1, 0.90, local=True, frais=True),
    _p("beurre", "Beurre doux 250 g Vallée Verte", "laitiers", 1, "marque", 2.45, 10, 40, 0.2, 0.75, frais=True),
    _p("oeufs_mdd", "Œufs x6 (premier prix)", "oeufs", 1, "mdd", 1.79, 6, 21, 0.3, 0.50),
    _p("oeufs_plein_air", "Œufs plein air x6 Ferme des Saules", "oeufs", 1, "marque", 2.45, 6, 21, 0.3, 0.75, local=True),
    _p("oeufs_bio", "Œufs bio x6", "oeufs", 1, "bio", 3.25, 6, 21, 0.3, 0.85, bio=True),
    _p("poulet_mdd", "Filets de poulet 500 g (premier prix)", "proteines", 1, "mdd", 5.60, 4, 4, 0.5, 0.50, viande=True, frais=True),
    _p("poulet_label", "Poulet fermier Label Rouge 1,2 kg", "proteines", 1, "premium", 10.90, 8, 5, 0.4, 0.90, viande=True, local=True, frais=True),
    _p("steak_hache", "Steaks hachés 5 % x4 Boucherie Delmas", "proteines", 1, "marque", 4.95, 4, 5, 0.6, 0.70, viande=True, frais=True),
    _p("jambon", "Jambon blanc x4 tranches", "proteines", 1, "mdd", 2.65, 2, 10, 0.6, 0.55, viande=True, porc=True, frais=True),
    _p("tofu_bio", "Tofu nature bio 250 g", "proteines", 1, "bio", 2.85, 3, 20, 0.0, 0.75, bio=True, frais=True),
    # Rayon 2 : épicerie salée
    _p("pates_mdd", "Coquillettes 500 g (premier prix)", "feculents", 2, "mdd", 0.69, 5, 720, 0.6, 0.45, gluten=True),
    _p("pates_marque", "Spaghetti 500 g Dolce Pasta", "feculents", 2, "marque", 1.45, 5, 720, 0.5, 0.75, gluten=True),
    _p("riz_mdd", "Riz long 1 kg (premier prix)", "feculents", 2, "mdd", 1.49, 10, 720, 0.3, 0.50),
    _p("riz_basmati", "Riz basmati 1 kg Taj Royal", "feculents", 2, "premium", 3.25, 10, 720, 0.3, 0.85),
    _p("lentilles", "Lentilles vertes 500 g", "proteines", 2, "mdd", 1.59, 5, 720, 0.1, 0.65, local=True),
    _p("thon", "Thon au naturel x3 Océane", "proteines", 2, "marque", 4.20, 3, 720, 0.3, 0.70, poisson=True),
    _p("conserve_haricots", "Haricots verts 800 g (conserve)", "legumes", 2, "mdd", 1.39, 4, 720, 0.2, 0.50),
    _p("sauce_tomate_mdd", "Sauce tomate 500 g (premier prix)", "epicerie", 2, "mdd", 1.05, 4, 365, 0.4, 0.45),
    _p("sauce_tomate_marque", "Sauce tomate basilic Dolce Pasta", "epicerie", 2, "marque", 1.95, 4, 365, 0.4, 0.75),
    _p("huile", "Huile de tournesol 1 L", "epicerie", 2, "mdd", 2.25, 12, 365, 0.0, 0.55),
    # Rayon 3 : petit-déjeuner et sucré
    _p("cereales_mdd", "Céréales chocolatées 500 g (premier prix)", "petit_dej", 3, "mdd", 1.89, 10, 180, 0.8, 0.45, gluten=True),
    _p("cereales_marque", "Céréales Croquin Choco 500 g", "petit_dej", 3, "marque", 3.65, 10, 180, 0.95, 0.70, gluten=True),
    _p("cafe_mdd", "Café moulu 250 g (premier prix)", "petit_dej", 3, "mdd", 2.45, 25, 365, 0.0, 0.45),
    _p("cafe_premium", "Café pur arabica 250 g Grand Cru Delmas", "petit_dej", 3, "premium", 4.95, 25, 365, 0.0, 0.90),
    _p("confiture", "Confiture de fraises 370 g Mamie Odette", "petit_dej", 3, "marque", 2.65, 15, 365, 0.6, 0.75),
    _p("biscuits_mdd", "Petits-beurre 200 g (premier prix)", "snacks", 3, "mdd", 1.15, 8, 180, 0.5, 0.45, gluten=True),
    _p("biscuits_marque", "Biscuits fourrés chocolat Croquin", "snacks", 3, "marque", 2.45, 8, 180, 0.95, 0.70, impulsion=True, gluten=True),
    _p("chocolat", "Tablette chocolat noir 100 g", "snacks", 3, "marque", 1.95, 4, 365, 0.4, 0.75, impulsion=True),
    _p("bonbons", "Bonbons Pétillou 200 g", "snacks", 3, "marque", 1.95, 5, 365, 1.0, 0.55, impulsion=True),
    # Rayon 4 : boissons et surgelés
    _p("eau", "Eau de source 6 x 1,5 L", "boissons", 4, "mdd", 2.15, 9, 365, 0.3, 0.60),
    _p("jus_orange", "Jus d'orange 1 L Soleil d'Or", "boissons", 4, "marque", 1.99, 4, 120, 0.7, 0.70),
    _p("soda", "Soda cola 1,5 L Pétillou", "boissons", 4, "marque", 1.79, 5, 180, 0.85, 0.50, impulsion=True),
    _p("biere", "Bière blonde 6 x 25 cl", "boissons", 4, "marque", 4.60, 6, 270, 0.0, 0.65, alcool=True),
    _p("vin", "Vin rouge Côtes du Rhône 75 cl", "boissons", 4, "premium", 5.90, 5, 720, 0.0, 0.75, alcool=True),
    _p("glace", "Crème glacée vanille 1 L", "snacks", 4, "marque", 3.55, 6, 365, 0.9, 0.70, impulsion=True),
    _p("poisson_pane", "Poisson pané x8 (surgelé) Océane", "proteines", 4, "marque", 3.95, 4, 180, 0.7, 0.60, poisson=True, gluten=True),
    _p("legumes_surgeles", "Haricots verts surgelés 1 kg", "legumes", 4, "mdd", 2.15, 8, 365, 0.2, 0.55),
    _p("pizza", "Pizza 4 fromages (surgelée)", "plats", 4, "marque", 3.25, 2, 180, 0.8, 0.55, impulsion=True, gluten=True),
    _p("plat_cuisine", "Lasagnes bolognaise 1 kg", "plats", 4, "marque", 4.95, 3, 6, 0.6, 0.60, viande=True, gluten=True, frais=True),
    # Rayon 5 : hygiène, entretien, bébé, animaux
    _p("papier_toilette", "Papier toilette x12", "hygiene", 5, "mdd", 4.95, 12, 9999, 0.0, 0.55),
    _p("gel_douche", "Gel douche 250 ml Fraîcheur", "hygiene", 5, "marque", 2.45, 15, 720, 0.2, 0.70),
    _p("dentifrice", "Dentifrice 75 ml", "hygiene", 5, "marque", 2.25, 30, 720, 0.1, 0.70),
    _p("lessive_mdd", "Lessive liquide 30 lavages (premier prix)", "entretien", 5, "mdd", 5.95, 30, 9999, 0.0, 0.50),
    _p("lessive_marque", "Lessive Éclat 30 lavages", "entretien", 5, "marque", 9.95, 30, 9999, 0.0, 0.85),
    _p("vaisselle", "Liquide vaisselle 750 ml", "entretien", 5, "mdd", 1.75, 30, 9999, 0.0, 0.55),
    _p("couches", "Couches taille 4 x40 Doudou", "bebe", 5, "marque", 12.90, 40, 9999, 0.0, 0.75),
    _p("petits_pots", "Petits pots légumes x4 Doudou", "bebe", 5, "marque", 3.25, 4, 365, 0.0, 0.70),
    _p("croquettes_chat", "Croquettes chat 2 kg Minou", "animaux", 5, "marque", 7.95, 20, 365, 0.0, 0.70, animal="chat"),
    _p("croquettes_chien", "Croquettes chien 4 kg Médor", "animaux", 5, "marque", 10.95, 20, 365, 0.0, 0.70, animal="chien"),
]}

# Assortiment de départ (identique dans les deux magasins) : tout sauf quelques références haut de gamme.
ASSORTIMENT_INITIAL = [pid for pid in PRODUCTS if pid not in ("poulet_label", "oranges_bio", "riz_basmati")]

# Panier de référence pour l'indice de prix (produits courants).
PANIER_REFERENCE = ["baguette", "lait_mdd", "lait_marque", "yaourts_mdd", "oeufs_mdd", "poulet_mdd", "steak_hache",
                    "pates_mdd", "riz_mdd", "pommes", "bananes", "carottes", "tomates", "cereales_mdd", "cafe_mdd",
                    "biscuits_mdd", "eau", "jus_orange", "papier_toilette", "lessive_mdd", "beurre", "jambon"]


def products_for_need(need: str) -> list[Product]:
    return [p for p in PRODUCTS.values() if p.besoin == need]


def is_allowed(product: Product, attrs: dict) -> bool:
    """Contraintes de régime et de foyer d'un client."""
    regime = attrs.get("regime", "omnivore")
    if regime == "vegetarien" and (product.viande or product.poisson):
        return False
    if regime == "halal" and (product.porc or product.alcool or product.id == "plat_cuisine"):
        return False
    if regime == "sans_gluten" and product.gluten:
        return False
    if product.alcool and not attrs.get("achete_alcool", False):
        return False
    if product.besoin == "animaux" and product.animal != attrs.get("animal", "aucun"):
        return False
    if product.besoin == "bebe" and not attrs.get("nb_bebes", 0):
        return False
    allergies = attrs.get("allergies", [])
    if "lactose" in allergies and product.besoin in ("lait", "laitiers") and not product.id == "tofu_bio":
        return False
    if "arachide" in allergies and product.id in ("biscuits_marque", "chocolat"):
        return False
    return True
