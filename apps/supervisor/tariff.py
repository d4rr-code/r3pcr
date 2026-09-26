import re


HS_SECTIONS = [
    (1, 'I', 'Live Animals; Animal Products', list(range(1, 6))),
    (2, 'II', 'Vegetable Products', list(range(6, 15))),
    (3, 'III', 'Animal or Vegetable Fats and Oils', [15]),
    (4, 'IV', 'Prepared Foodstuffs; Beverages; Tobacco', list(range(16, 25))),
    (5, 'V', 'Mineral Products', list(range(25, 28))),
    (6, 'VI', 'Chemical or Allied Industry Products', list(range(28, 39))),
    (7, 'VII', 'Plastics and Rubber', [39, 40]),
    (8, 'VIII', 'Raw Hides, Leather, Furskins', list(range(41, 44))),
    (9, 'IX', 'Wood, Cork, Straw', list(range(44, 47))),
    (10, 'X', 'Pulp of Wood, Paper, Paperboard', list(range(47, 50))),
    (11, 'XI', 'Textiles and Textile Articles', list(range(50, 64))),
    (12, 'XII', 'Footwear, Headgear, Umbrellas', list(range(64, 68))),
    (13, 'XIII', 'Articles of Stone, Ceramics, Glass', list(range(68, 71))),
    (14, 'XIV', 'Precious Stones, Precious Metals', [71]),
    (15, 'XV', 'Base Metals and Articles', list(range(72, 84))),
    (16, 'XVI', 'Machinery and Mechanical Appliances', [84, 85]),
    (17, 'XVII', 'Vehicles, Aircraft, Vessels', list(range(86, 90))),
    (18, 'XVIII', 'Optical, Photographic, Medical Instruments', list(range(90, 93))),
    (19, 'XIX', 'Arms and Ammunition', [93]),
    (20, 'XX', 'Miscellaneous Manufactured Articles', list(range(94, 97))),
    (21, 'XXI', 'Works of Art, Collectors Pieces, Antiques', [97]),
]


def chapter_number(chapter):
    """Extract the number from values such as ``Chapter 84`` or ``01``."""
    if not chapter:
        return None
    match = re.search(r'\d+', str(chapter))
    return int(match.group()) if match else None


# Compatibility aliases for the existing internal import surface.
_HS_SECTIONS = HS_SECTIONS
_chapter_num = chapter_number
