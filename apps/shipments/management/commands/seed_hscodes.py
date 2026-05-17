from django.core.management.base import BaseCommand
from apps.shipments.models import HSCode


class Command(BaseCommand):
    help = 'Seed HS Codes based on the Philippine AHTN 2022 Tariff Book'

    def handle(self, *args, **kwargs):
        # Format: (code, description, duty_rate %, unit, chapter)
        # Duty rates follow Philippine MFN rates under CMTA / EO 82 s.2019 / AHTN 2022
        hs_codes = [

            # ── Chapter 01 — Live Animals ─────────────────────────────────────
            ('0101.21.00', 'Live horses, pure-bred breeding', 0.00, 'head', '01'),
            ('0106.11.00', 'Live primates', 0.00, 'head', '01'),
            ('0106.19.00', 'Other live mammals', 0.00, 'head', '01'),

            # ── Chapter 02 — Meat & Edible Offal ─────────────────────────────
            ('0201.10.00', 'Carcasses and half-carcasses of bovine animals, fresh or chilled', 5.00, 'kg', '02'),
            ('0201.20.00', 'Other cuts of bovine animals with bone in, fresh or chilled', 5.00, 'kg', '02'),
            ('0207.12.00', 'Frozen whole chickens', 5.00, 'kg', '02'),
            ('0207.14.00', 'Frozen cuts and offal of chickens', 5.00, 'kg', '02'),
            ('0210.11.00', 'Hams, shoulders and cuts thereof of swine, salted/dried/smoked', 5.00, 'kg', '02'),

            # ── Chapter 03 — Fish & Seafood ───────────────────────────────────
            ('0302.11.00', 'Fresh or chilled trout', 5.00, 'kg', '03'),
            ('0303.89.00', 'Other frozen fish, not elsewhere specified', 5.00, 'kg', '03'),
            ('0306.17.00', 'Frozen shrimps and prawns', 5.00, 'kg', '03'),
            ('0307.49.00', 'Frozen cuttlefish and squid, other', 5.00, 'kg', '03'),
            ('0305.20.00', 'Livers and roes of fish, dried, smoked, salted or in brine', 5.00, 'kg', '03'),

            # ── Chapter 04 — Dairy, Eggs, Honey ──────────────────────────────
            ('0401.10.00', 'Milk and cream, not concentrated, fat content ≤1%', 7.00, 'L', '04'),
            ('0402.10.00', 'Milk powder, fat content ≤1.5%', 7.00, 'kg', '04'),
            ('0406.10.00', 'Fresh (unripened) cheese', 7.00, 'kg', '04'),
            ('0407.11.00', 'Fertilised eggs of fowls of the species Gallus domesticus', 7.00, 'unit', '04'),
            ('0409.00.00', 'Natural honey', 7.00, 'kg', '04'),

            # ── Chapter 07 — Vegetables ───────────────────────────────────────
            ('0702.00.00', 'Tomatoes, fresh or chilled', 7.00, 'kg', '07'),
            ('0703.10.00', 'Onions and shallots, fresh or chilled', 7.00, 'kg', '07'),
            ('0714.10.00', 'Manioc (cassava), fresh, chilled, frozen or dried', 7.00, 'kg', '07'),

            # ── Chapter 08 — Fruits & Nuts ────────────────────────────────────
            ('0803.90.00', 'Fresh bananas, other', 5.00, 'kg', '08'),
            ('0805.10.00', 'Oranges, fresh or dried', 5.00, 'kg', '08'),
            ('0901.11.10', 'Coffee, not roasted, not decaffeinated', 5.00, 'kg', '09'),

            # ── Chapter 09 — Coffee, Tea, Spices ─────────────────────────────
            ('0902.10.00', 'Green tea (not fermented), in packages ≤3 kg', 7.00, 'kg', '09'),
            ('0902.40.00', 'Other black tea (fermented) and partly fermented tea', 7.00, 'kg', '09'),
            ('0904.21.00', 'Pepper, dried, neither crushed nor ground', 7.00, 'kg', '09'),
            ('0910.11.00', 'Ginger, neither crushed nor ground', 7.00, 'kg', '09'),

            # ── Chapter 10 — Cereals ──────────────────────────────────────────
            ('1001.19.00', 'Other wheat and meslin, not durum wheat', 3.00, 'kg', '10'),
            ('1006.10.00', 'Husked (brown) rice', 35.00, 'kg', '10'),
            ('1006.20.00', 'Husked rice', 35.00, 'kg', '10'),
            ('1006.30.10', 'Semi-milled or wholly milled rice, for human consumption', 35.00, 'kg', '10'),
            ('1006.40.00', 'Broken rice', 35.00, 'kg', '10'),

            # ── Chapter 11 — Milling Industry Products ────────────────────────
            ('1101.00.00', 'Wheat or meslin flour', 3.00, 'kg', '11'),
            ('1102.20.00', 'Maize (corn) flour', 3.00, 'kg', '11'),
            ('1108.12.00', 'Maize (corn) starch', 3.00, 'kg', '11'),

            # ── Chapter 15 — Oils & Fats ──────────────────────────────────────
            ('1511.10.00', 'Crude palm oil for food manufacture', 5.00, 'kg', '15'),
            ('1511.90.00', 'Other palm oil and fractions', 5.00, 'kg', '15'),
            ('1516.20.00', 'Hydrogenated vegetable fats and oils (margarine base)', 5.00, 'kg', '15'),
            ('1517.10.00', 'Margarine, excluding liquid margarine', 7.00, 'kg', '15'),

            # ── Chapter 16 — Prepared Meat / Fish ────────────────────────────
            ('1601.00.00', 'Sausages and similar products of meat or offal', 7.00, 'kg', '16'),
            ('1602.32.00', 'Other prepared/preserved meat of chickens', 7.00, 'kg', '16'),
            ('1604.14.00', 'Prepared or preserved tunas, skipjack and bonito', 7.00, 'kg', '16'),

            # ── Chapter 17 — Sugar & Confectionery ───────────────────────────
            ('1701.14.00', 'Cane sugar, other raw sugar not containing added flavoring or coloring', 5.00, 'kg', '17'),
            ('1702.30.00', 'Glucose and glucose syrup, not containing fructose', 7.00, 'kg', '17'),
            ('1704.90.00', 'Sugar confectionery not containing cocoa (candies, chewing gum)', 7.00, 'kg', '17'),

            # ── Chapter 18 — Cocoa & Chocolate ───────────────────────────────
            ('1801.00.00', 'Cocoa beans, whole or broken, raw or roasted', 0.00, 'kg', '18'),
            ('1805.00.00', 'Cocoa powder, not containing added sugar or sweetening matter', 7.00, 'kg', '18'),
            ('1806.10.00', 'Cocoa powder, containing added sugar or sweetening matter', 7.00, 'kg', '18'),
            ('1806.20.00', 'Chocolate and cocoa preparations in blocks/slabs/bars > 2 kg', 7.00, 'kg', '18'),
            ('1806.90.00', 'Chocolate and other food preparations containing cocoa (other)', 7.00, 'kg', '18'),

            # ── Chapter 19 — Preparations of Flour / Starch ──────────────────
            ('1901.10.00', 'Preparations for infant use, put up for retail sale', 0.00, 'kg', '19'),
            ('1901.20.00', 'Mixes and doughs for preparation of bakers wares', 7.00, 'kg', '19'),
            ('1902.11.00', 'Uncooked pasta, not stuffed, containing eggs', 7.00, 'kg', '19'),
            ('1902.30.00', 'Other pasta, including instant noodles', 7.00, 'kg', '19'),
            ('1905.31.00', 'Sweet biscuits', 7.00, 'kg', '19'),
            ('1905.32.00', 'Waffles and wafers', 7.00, 'kg', '19'),
            ('1905.90.00', 'Other bread, pastry, cakes, biscuits and bakers wares', 7.00, 'kg', '19'),
            ('1901.90.99', 'Food preparations of flour, groats, starch or malt extract (other)', 7.00, 'kg', '19'),

            # ── Chapter 20 — Preparations of Vegetables / Fruits ─────────────
            ('2002.10.00', 'Tomatoes prepared or preserved, whole or in pieces', 7.00, 'kg', '20'),
            ('2005.20.00', 'Potatoes, prepared or preserved, not frozen', 7.00, 'kg', '20'),
            ('2007.91.00', 'Jams, jellies and marmalades of citrus fruit', 7.00, 'kg', '20'),
            ('2009.12.00', 'Frozen orange juice', 7.00, 'L', '20'),
            ('2009.89.90', 'Juice of other fruit or vegetables, unfermented (other)', 7.00, 'L', '20'),

            # ── Chapter 21 — Miscellaneous Edible Preparations ───────────────
            ('2101.11.00', 'Extracts, essences and concentrates of coffee', 7.00, 'kg', '21'),
            ('2101.20.00', 'Extracts, essences and concentrates of tea or mate', 7.00, 'kg', '21'),
            ('2103.20.00', 'Tomato ketchup and other tomato sauces', 7.00, 'kg', '21'),
            ('2103.90.00', 'Sauces and preparations; condiments, seasonings (other)', 7.00, 'kg', '21'),
            ('2104.10.00', 'Soups and broths and preparations therefor', 7.00, 'kg', '21'),
            ('2106.90.99', 'Food preparations not elsewhere specified (protein supplements, etc.)', 7.00, 'kg', '21'),

            # ── Chapter 22 — Beverages, Spirits and Vinegar ──────────────────
            ('2202.10.00', 'Waters, including mineral waters and aerated waters, sweetened', 7.00, 'L', '22'),
            ('2202.91.00', 'Non-alcoholic beer', 7.00, 'L', '22'),
            ('2202.99.10', 'Energy drinks', 7.00, 'L', '22'),
            ('2203.00.00', 'Beer made from malt', 20.00, 'L', '22'),
            ('2208.40.00', 'Rum and other spirits from fermented sugarcane products', 20.00, 'L', '22'),
            ('2208.70.00', 'Liqueurs and cordials', 20.00, 'L', '22'),

            # ── Chapter 25 — Salt, Sulphur, Earths, Stone ────────────────────
            ('2523.10.00', 'Cement clinkers', 3.00, 'kg', '25'),
            ('2523.21.00', 'White portland cement', 3.00, 'kg', '25'),
            ('2523.29.00', 'Other portland cement', 3.00, 'kg', '25'),

            # ── Chapter 27 — Mineral Fuels & Oils ────────────────────────────
            ('2710.12.19', 'Motor gasoline (petrol), other', 3.00, 'L', '27'),
            ('2710.19.21', 'Diesel fuel oil (gas oil)', 3.00, 'L', '27'),
            ('2711.12.00', 'Propane (LPG), liquefied', 3.00, 'kg', '27'),

            # ── Chapter 28 — Inorganic Chemicals ─────────────────────────────
            ('2835.25.00', 'Calcium hydrogenorthophosphate (dicalcium phosphate)', 0.00, 'kg', '28'),
            ('2836.20.00', 'Disodium carbonate (soda ash)', 0.00, 'kg', '28'),

            # ── Chapter 29 — Organic Chemicals ───────────────────────────────
            ('2915.21.00', 'Acetic acid', 0.00, 'kg', '29'),
            ('2921.41.00', 'Aniline', 0.00, 'kg', '29'),

            # ── Chapter 30 — Pharmaceutical Products ─────────────────────────
            ('3001.90.00', 'Other glands and organs and their extracts', 0.00, 'kg', '30'),
            ('3002.12.00', 'Antisera and other blood fractions and modified immunological products', 0.00, 'kg', '30'),
            ('3004.10.00', 'Medicaments containing penicillins or derivatives', 0.00, 'kg', '30'),
            ('3004.20.00', 'Medicaments containing antibiotics, not penicillin-based', 0.00, 'kg', '30'),
            ('3004.31.00', 'Medicaments containing insulin', 0.00, 'kg', '30'),
            ('3004.39.00', 'Medicaments containing hormones, other', 0.00, 'kg', '30'),
            ('3004.50.00', 'Medicaments containing vitamins or provitamins', 0.00, 'kg', '30'),
            ('3004.90.10', 'Medicaments for veterinary use', 0.00, 'kg', '30'),
            ('3004.90.90', 'Medicaments for therapeutic or prophylactic use, other', 0.00, 'kg', '30'),
            ('3005.10.00', 'Adhesive dressings and other articles having an adhesive layer (bandages)', 0.00, 'kg', '30'),
            ('3006.10.00', 'Sterile surgical catgut, suture materials', 0.00, 'kg', '30'),
            ('3006.50.00', 'First-aid boxes and kits', 0.00, 'kg', '30'),

            # ── Chapter 32 — Paints, Inks, Adhesives ─────────────────────────
            ('3208.10.00', 'Paints and varnishes based on polyesters, in non-aqueous medium', 3.00, 'kg', '32'),
            ('3209.10.00', 'Paints and varnishes based on acrylic or vinyl polymers, in aqueous medium', 3.00, 'kg', '32'),
            ('3214.10.00', 'Glaziers putty, grafting putty, resin cements, caulking compounds', 3.00, 'kg', '32'),
            ('3215.11.00', 'Black printing ink', 0.00, 'kg', '32'),
            ('3215.19.00', 'Other printing ink', 0.00, 'kg', '32'),

            # ── Chapter 33 — Cosmetics & Toiletries ──────────────────────────
            ('3303.00.00', 'Perfumes and toilet waters', 10.00, 'kg', '33'),
            ('3304.10.00', 'Lip make-up preparations', 10.00, 'kg', '33'),
            ('3304.20.00', 'Eye make-up preparations', 10.00, 'kg', '33'),
            ('3304.30.00', 'Manicure or pedicure preparations', 10.00, 'kg', '33'),
            ('3304.91.00', 'Powders, whether or not compressed, for skin care', 10.00, 'kg', '33'),
            ('3304.99.00', 'Beauty or make-up preparations; skin-care preparations (other)', 10.00, 'kg', '33'),
            ('3305.10.00', 'Shampoos', 10.00, 'L', '33'),
            ('3305.20.00', 'Preparations for permanent waving or straightening of hair', 10.00, 'kg', '33'),
            ('3305.30.00', 'Hair lacquers', 10.00, 'kg', '33'),
            ('3306.10.00', 'Dentifrices (toothpaste, tooth powder)', 10.00, 'kg', '33'),
            ('3307.10.00', 'Pre-shave, shaving or after-shave preparations', 10.00, 'kg', '33'),
            ('3307.20.00', 'Personal deodorants and antiperspirants', 10.00, 'kg', '33'),
            ('3307.41.00', 'Agarbatti and other odoriferous preparations', 10.00, 'kg', '33'),

            # ── Chapter 34 — Soap, Detergents ────────────────────────────────
            ('3401.11.00', 'Soap for toilet use (including medicated)', 7.00, 'kg', '34'),
            ('3401.20.00', 'Soap in other forms (flakes, powder, granules, paste)', 7.00, 'kg', '34'),
            ('3402.20.00', 'Preparations put up for retail sale (laundry detergent, dishwashing)', 7.00, 'kg', '34'),

            # ── Chapter 38 — Miscellaneous Chemical Products ──────────────────
            ('3808.91.00', 'Insecticides for retail sale or as preparations', 7.00, 'kg', '38'),
            ('3808.94.00', 'Disinfectants', 7.00, 'kg', '38'),
            ('3820.00.00', 'Anti-freezing preparations and prepared de-icing fluids', 3.00, 'kg', '38'),
            ('3824.99.99', 'Other chemical preparations not elsewhere specified', 3.00, 'kg', '38'),

            # ── Chapter 39 — Plastics ─────────────────────────────────────────
            ('3901.10.00', 'Polyethylene having a specific gravity < 0.94, in primary forms', 3.00, 'kg', '39'),
            ('3902.10.00', 'Polypropylene, in primary forms', 3.00, 'kg', '39'),
            ('3904.10.00', 'Polyvinyl chloride (PVC), not mixed with any other substance', 3.00, 'kg', '39'),
            ('3917.32.00', 'Other flexible tubes, pipes and hoses of plastics', 3.00, 'kg', '39'),
            ('3919.10.00', 'Self-adhesive plates/sheets/film of plastics, in rolls ≤20 cm wide', 3.00, 'kg', '39'),
            ('3919.90.00', 'Self-adhesive plates, sheets and film of plastics (other)', 3.00, 'kg', '39'),
            ('3923.10.00', 'Boxes, cases, crates and similar articles of plastics', 3.00, 'kg', '39'),
            ('3923.21.00', 'Sacks and bags of polymers of ethylene', 3.00, 'kg', '39'),
            ('3923.30.00', 'Carboys, bottles, flasks and similar articles of plastics', 3.00, 'kg', '39'),
            ('3924.10.00', 'Tableware and kitchenware of plastics', 7.00, 'kg', '39'),
            ('3926.10.00', 'Office or school supplies of plastics', 5.00, 'kg', '39'),
            ('3926.20.00', 'Articles of apparel and clothing accessories of plastics', 7.00, 'kg', '39'),
            ('3926.90.99', 'Other articles of plastics (other)', 3.00, 'kg', '39'),

            # ── Chapter 40 — Rubber ───────────────────────────────────────────
            ('4011.10.00', 'New pneumatic tyres of rubber for motor cars', 3.00, 'unit', '40'),
            ('4011.20.00', 'New pneumatic tyres of rubber for buses or lorries', 3.00, 'unit', '40'),
            ('4011.40.00', 'New pneumatic tyres of rubber for motorcycles', 3.00, 'unit', '40'),
            ('4015.11.00', 'Surgical gloves of vulcanized rubber', 0.00, 'unit', '40'),
            ('4015.19.00', 'Other gloves of vulcanized rubber', 3.00, 'unit', '40'),
            ('4016.99.90', 'Other articles of vulcanized rubber (other)', 3.00, 'kg', '40'),

            # ── Chapter 42 — Leather Goods, Bags ─────────────────────────────
            ('4202.11.00', 'Trunks and suitcases with outer surface of leather', 10.00, 'unit', '42'),
            ('4202.12.00', 'Trunks and suitcases with outer surface of plastics or textile', 10.00, 'unit', '42'),
            ('4202.21.00', 'Handbags, with outer surface of leather', 10.00, 'unit', '42'),
            ('4202.22.00', 'Handbags, with outer surface of plastics sheeting or textile', 10.00, 'unit', '42'),
            ('4202.31.00', 'Articles of a kind normally carried in the pocket, of leather', 10.00, 'unit', '42'),
            ('4203.10.00', 'Articles of apparel of leather (jackets, coats)', 10.00, 'unit', '42'),

            # ── Chapter 44 — Wood & Wood Articles ────────────────────────────
            ('4407.10.00', 'Coniferous wood, sawn or chipped lengthwise', 0.00, 'cu.m', '44'),
            ('4418.10.00', 'Doors and their frames and thresholds of wood', 3.00, 'unit', '44'),
            ('4418.20.00', 'Windows, French-windows and their frames of wood', 3.00, 'unit', '44'),
            ('4421.91.00', 'Other articles of wood (wooden hangers, blinds, etc.)', 3.00, 'kg', '44'),

            # ── Chapter 48 — Paper & Paperboard ──────────────────────────────
            ('4802.55.00', 'Writing paper, in rolls or sheets, weight 40–150 g/m2', 0.00, 'kg', '48'),
            ('4811.41.00', 'Self-adhesive paper and paperboard, in rolls or sheets', 0.00, 'kg', '48'),
            ('4819.10.00', 'Cartons, boxes of corrugated paper or paperboard', 0.00, 'kg', '48'),
            ('4820.10.00', 'Registers, account books, notebooks, diaries of paper', 0.00, 'kg', '48'),
            ('4823.90.00', 'Other paper, paperboard, cellulose wadding (other articles)', 0.00, 'kg', '48'),

            # ── Chapter 49 — Printed Matter ───────────────────────────────────
            ('4901.10.00', 'Printed books, brochures, in single sheets', 0.00, 'kg', '49'),
            ('4901.99.00', 'Other printed books, brochures, leaflets and similar', 0.00, 'kg', '49'),
            ('4902.90.00', 'Newspapers, journals and periodicals (other)', 0.00, 'kg', '49'),
            ('4911.10.00', 'Trade advertising material, commercial catalogues', 0.00, 'kg', '49'),

            # ── Chapter 54 — Man-made Filaments ──────────────────────────────
            ('5407.61.00', 'Woven fabrics of non-textured polyester filament yarn, ≥85%', 5.00, 'kg', '54'),

            # ── Chapter 55 — Man-made Staple Fibres ──────────────────────────
            ('5512.11.00', 'Woven fabrics of synthetic staple fibres, ≥85% polyester', 5.00, 'kg', '55'),

            # ── Chapter 57 — Carpets & Floor Coverings ────────────────────────
            ('5703.20.00', 'Carpets and other textile floor coverings, tufted, of nylon or other polyamides', 7.00, 'sq.m', '57'),
            ('5705.00.00', 'Other carpets and other textile floor coverings (other)', 7.00, 'sq.m', '57'),

            # ── Chapter 61-62 — Clothing & Garments ──────────────────────────
            ('6101.20.00', 'Men\'s overcoats, car coats, of cotton, knitted', 10.00, 'kg', '61'),
            ('6104.62.00', 'Women\'s trousers and shorts of cotton, knitted', 10.00, 'kg', '61'),
            ('6105.10.00', 'Men\'s shirts of cotton, knitted', 10.00, 'kg', '61'),
            ('6109.10.00', 'T-shirts, singlets and other vests, of cotton, knitted', 10.00, 'kg', '61'),
            ('6109.90.00', 'T-shirts, singlets and other vests, of other textile material', 10.00, 'kg', '61'),
            ('6111.20.00', 'Babies\' garments and clothing accessories of cotton, knitted', 10.00, 'kg', '61'),
            ('6115.22.00', 'Hosiery of cotton, knitted; women\'s full-length or knee-length stockings', 10.00, 'pair', '61'),
            ('6201.12.00', 'Overcoats, car coats of cotton, for men/boys', 10.00, 'kg', '62'),
            ('6202.12.00', 'Overcoats, car coats of cotton, for women/girls', 10.00, 'kg', '62'),
            ('6203.42.00', 'Men\'s trousers and shorts of cotton, not knitted', 10.00, 'kg', '62'),
            ('6204.62.00', 'Women\'s trousers and shorts of cotton, not knitted', 10.00, 'kg', '62'),
            ('6205.20.00', 'Men\'s shirts of cotton, not knitted', 10.00, 'kg', '62'),
            ('6206.10.00', 'Women\'s blouses and shirts of silk or silk waste', 10.00, 'kg', '62'),
            ('6211.43.00', 'Men\'s garments of man-made fibers (tracksuit, etc.)', 10.00, 'kg', '62'),
            ('6211.44.00', 'Women\'s garments of man-made fibers (activewear, etc.)', 10.00, 'kg', '62'),
            ('6217.10.00', 'Accessories for clothing (scarves, ties, handkerchiefs, etc.)', 10.00, 'kg', '62'),
            ('6216.00.00', 'Gloves, mittens and mitts', 10.00, 'pair', '62'),

            # ── Chapter 63 — Other Made-up Textile Articles ───────────────────
            ('6302.10.00', 'Bed linen, knitted or crocheted', 7.00, 'kg', '63'),
            ('6302.21.00', 'Other bed linen of cotton, printed', 7.00, 'kg', '63'),
            ('6303.92.00', 'Curtains, drapes and interior blinds of synthetic fibers', 7.00, 'kg', '63'),
            ('6305.33.00', 'Sacks and bags for packing of polyethylene or polypropylene strip', 3.00, 'kg', '63'),

            # ── Chapter 64 — Footwear ─────────────────────────────────────────
            ('6401.92.00', 'Waterproof footwear with rubber/plastic outer soles, covering ankle only', 10.00, 'pair', '64'),
            ('6402.99.00', 'Other footwear with rubber or plastics outer soles and uppers', 10.00, 'pair', '64'),
            ('6403.91.00', 'Footwear with leather uppers, covering the ankle', 10.00, 'pair', '64'),
            ('6403.99.00', 'Footwear with leather uppers, other (not covering ankle)', 10.00, 'pair', '64'),
            ('6404.11.00', 'Footwear with rubber/plastic outer soles, textile uppers (sports)', 10.00, 'pair', '64'),
            ('6404.19.00', 'Other footwear with rubber/plastics outer soles and textile uppers', 10.00, 'pair', '64'),
            ('6405.20.00', 'Footwear with textile uppers, other', 10.00, 'pair', '64'),

            # ── Chapter 68-70 — Stone, Ceramic, Glass ────────────────────────
            ('6802.91.00', 'Other monumental or building stone (marble, travertine)', 5.00, 'kg', '68'),
            ('6910.10.00', 'Ceramic sinks, washbasins, baths, bidets of porcelain/china', 5.00, 'unit', '69'),
            ('7013.22.00', 'Drinking glasses, other than of glass-ceramics, lead crystal', 5.00, 'unit', '70'),
            ('7013.49.00', 'Glassware used for table or kitchen purposes (other)', 5.00, 'unit', '70'),

            # ── Chapter 72-73 — Steel & Iron ──────────────────────────────────
            ('7208.51.00', 'Flat-rolled products of iron or non-alloy steel (thickness > 10 mm)', 3.00, 'kg', '72'),
            ('7209.16.00', 'Flat-rolled products of iron or steel, cold-rolled (0.5-1 mm)', 3.00, 'kg', '72'),
            ('7210.49.00', 'Flat-rolled products of iron, zinc-coated (galvanized, other)', 3.00, 'kg', '72'),
            ('7214.20.00', 'Bars/rods of iron or non-alloy steel, twisted/with indentations (rebar)', 3.00, 'kg', '72'),
            ('7216.21.00', 'L-sections of iron or non-alloy steel (angles, not further worked)', 3.00, 'kg', '72'),
            ('7304.31.00', 'Cold-drawn or cold-rolled seamless tubes of circular cross-section', 3.00, 'kg', '73'),
            ('7318.15.00', 'Screws and bolts for metal, iron or steel', 3.00, 'kg', '73'),
            ('7323.93.00', 'Table, kitchen or household articles of stainless steel', 5.00, 'kg', '73'),

            # ── Chapter 74 — Copper ───────────────────────────────────────────
            ('7408.11.00', 'Copper wire, of refined copper, cross-sectional area > 6 mm2', 3.00, 'kg', '74'),
            ('7418.10.00', 'Table, kitchen or household articles of copper', 5.00, 'kg', '74'),

            # ── Chapter 76 — Aluminum ─────────────────────────────────────────
            ('7604.10.00', 'Bars, rods and profiles of aluminum, not alloyed', 3.00, 'kg', '76'),
            ('7606.11.00', 'Plates, sheets and strip of aluminum, not alloyed, rectangular', 3.00, 'kg', '76'),
            ('7615.10.00', 'Table, kitchen or household articles of aluminum', 5.00, 'kg', '76'),

            # ── Chapter 82-83 — Tools & Miscellaneous Metal Articles ──────────
            ('8201.10.00', 'Spades and shovels of base metal', 5.00, 'unit', '82'),
            ('8203.20.00', 'Pliers, pincers and similar tools of base metal', 5.00, 'unit', '82'),
            ('8205.59.00', 'Other hand tools (screwdrivers, wrenches, etc.) of base metal', 5.00, 'unit', '82'),
            ('8211.92.00', 'Knives with fixed blades, other', 5.00, 'unit', '82'),
            ('8301.40.00', 'Locks of a kind used on furniture, luggage, etc.', 5.00, 'unit', '83'),
            ('8302.42.00', 'Mountings, fittings for furniture (hinges, handles)', 5.00, 'unit', '83'),
            ('8306.21.00', 'Statuettes and ornaments of base metal, silver-plated', 5.00, 'unit', '83'),
            ('8307.10.00', 'Flexible tubing of iron or steel', 3.00, 'unit', '83'),

            # ── Chapter 84 — Machinery & Mechanical Appliances ────────────────
            ('8415.10.00', 'Air conditioning machines, window/wall type', 5.00, 'unit', '84'),
            ('8418.10.00', 'Combined refrigerator-freezers', 5.00, 'unit', '84'),
            ('8418.21.00', 'Household-type compression refrigerators', 5.00, 'unit', '84'),
            ('8419.89.00', 'Machinery for treating material by temperature change (industrial)', 3.00, 'unit', '84'),
            ('8421.21.00', 'Water filtering or purifying machinery and apparatus', 3.00, 'unit', '84'),
            ('8422.11.00', 'Dishwashing machines of the household type', 5.00, 'unit', '84'),
            ('8422.30.00', 'Machinery for filling, closing, sealing bottles/cans', 5.00, 'unit', '84'),
            ('8450.11.00', 'Fully-automatic household washing machines (<=10 kg)', 5.00, 'unit', '84'),
            ('8450.12.00', 'Household washing machines, top-loading type', 5.00, 'unit', '84'),
            ('8451.21.00', 'Drying machines for household use (<=10 kg)', 5.00, 'unit', '84'),
            ('8452.10.00', 'Sewing machines, household type', 5.00, 'unit', '84'),
            ('8467.11.00', 'Rotary type pneumatic tools (drills, sanders)', 5.00, 'unit', '84'),
            ('8467.21.00', 'Drills of all kinds, with self-contained electric motor', 5.00, 'unit', '84'),
            ('8471.30.00', 'Portable automatic data processing machines (laptops/notebooks, <=10 kg)', 0.00, 'unit', '84'),
            ('8471.41.00', 'Other automatic data processing machines (desktop computers)', 0.00, 'unit', '84'),
            ('8471.49.00', 'Other data processing systems (servers, workstations)', 0.00, 'unit', '84'),
            ('8471.60.00', 'Input/output units for ADP machines (keyboards, mice, scanners)', 0.00, 'unit', '84'),
            ('8473.30.00', 'Parts and accessories for computers (RAM, HDD, SSD, etc.)', 0.00, 'unit', '84'),
            ('8481.80.00', 'Taps, cocks, valves and similar appliances', 5.00, 'unit', '84'),
            ('8483.40.00', 'Gears and gearing; ball and roller screws; gear boxes', 3.00, 'unit', '84'),
            ('8508.11.00', 'Vacuum cleaners, power <=1500 W, bag capacity <=20 L', 5.00, 'unit', '84'),
            ('8516.10.00', 'Electric instantaneous or storage water heaters and immersion heaters', 5.00, 'unit', '84'),
            ('8516.31.00', 'Hair dryers', 5.00, 'unit', '84'),
            ('8516.40.00', 'Electric smoothing irons', 5.00, 'unit', '84'),
            ('8516.50.00', 'Microwave ovens', 5.00, 'unit', '84'),
            ('8516.60.00', 'Other ovens; cookers, cooking plates, boiling rings, electric toasters', 5.00, 'unit', '84'),
            ('8516.71.00', 'Coffee or tea makers for household use', 5.00, 'unit', '84'),
            ('8516.72.00', 'Toasters', 5.00, 'unit', '84'),
            ('8517.13.00', 'Smartphones, telephones for cellular networks (other)', 0.00, 'unit', '84'),

            # ── Chapter 85 — Electrical Machinery & Equipment ─────────────────
            ('8501.10.00', 'Electric motors of an output <=37.5 W', 0.00, 'unit', '85'),
            ('8504.40.00', 'Static converters (inverters, chargers, adapters)', 0.00, 'unit', '85'),
            ('8507.10.00', 'Lead-acid storage batteries for starting piston engines', 3.00, 'unit', '85'),
            ('8507.60.00', 'Lithium-ion batteries', 0.00, 'unit', '85'),
            ('8517.12.00', 'Telephones for cellular networks, smartphones', 0.00, 'unit', '85'),
            ('8517.62.00', 'Machines for the reception, conversion and transmission of data (routers, switches)', 0.00, 'unit', '85'),
            ('8518.21.00', 'Single loudspeakers, mounted in their enclosures', 3.00, 'unit', '85'),
            ('8518.30.00', 'Headphones, earphones, and combined microphone/speaker sets', 3.00, 'unit', '85'),
            ('8518.40.00', 'Audio-frequency electric amplifiers', 3.00, 'unit', '85'),
            ('8518.22.00', 'Multiple loudspeakers, mounted in the same enclosure (speaker sets)', 3.00, 'unit', '85'),
            ('8521.10.00', 'Magnetic tape-type video recording/reproducing apparatus', 3.00, 'unit', '85'),
            ('8523.51.00', 'Solid-state non-volatile storage devices (USB flash drives, memory cards)', 0.00, 'unit', '85'),
            ('8525.60.00', 'Transmission apparatus for radio-broadcasting/TV (cameras)', 0.00, 'unit', '85'),
            ('8525.80.00', 'Television cameras, digital cameras and video camera recorders', 0.00, 'unit', '85'),
            ('8528.72.00', 'Television receivers, color, capable of receiving analog/digital signals', 5.00, 'unit', '85'),
            ('8534.00.00', 'Printed circuits', 0.00, 'unit', '85'),
            ('8536.50.00', 'Switches, other than relays (<=1000 V)', 3.00, 'unit', '85'),
            ('8543.70.00', 'Electric machines and apparatus, not elsewhere specified (e-cigarettes, etc.)', 3.00, 'unit', '85'),
            ('8544.42.00', 'Electric conductors/wires, fitted with connectors (voltage <=1000 V)', 3.00, 'unit', '85'),
            ('8544.60.00', 'Other electric conductors for a voltage exceeding 1000 V', 3.00, 'kg', '85'),

            # ── Chapter 86-87 — Vehicles ──────────────────────────────────────
            ('8703.10.00', 'Vehicles specially designed for travelling on snow; golf cars', 5.00, 'unit', '87'),
            ('8703.23.19', 'Motor cars, spark-ignition, 1500-3000 cc (gasoline, not CKD)', 30.00, 'unit', '87'),
            ('8703.40.91', 'Motor cars, plug-in hybrid electric, not CKD', 5.00, 'unit', '87'),
            ('8703.80.91', 'Motor cars, battery electric, not CKD', 5.00, 'unit', '87'),
            ('8704.21.19', 'Motor vehicles for transport of goods, diesel, GVW <=5 tonnes (other)', 15.00, 'unit', '87'),
            ('8711.60.00', 'Motorcycles, electric', 5.00, 'unit', '87'),
            ('8708.29.90', 'Parts and accessories of motor vehicles (body parts, bumpers)', 5.00, 'unit', '87'),
            ('8708.99.90', 'Other parts and accessories of motor vehicles', 5.00, 'unit', '87'),
            ('8712.00.00', 'Bicycles and other cycles, non-motorized', 10.00, 'unit', '87'),
            ('8714.99.90', 'Parts and accessories for cycles (other)', 10.00, 'unit', '87'),
            ('8716.40.00', 'Other trailers and semi-trailers', 5.00, 'unit', '87'),

            # ── Chapter 88 — Aircraft & Parts ─────────────────────────────────
            ('8806.21.00', 'Unmanned aircraft (drones), for recreational use', 0.00, 'unit', '88'),
            ('8806.29.00', 'Other unmanned aircraft', 0.00, 'unit', '88'),

            # ── Chapter 89 — Ships & Boats ────────────────────────────────────
            ('8901.10.00', 'Cruise ships, excursion boats and similar for transport of persons', 0.00, 'unit', '89'),
            ('8903.92.00', 'Motorboats (other than outboard motorboats)', 0.00, 'unit', '89'),

            # ── Chapter 90 — Medical & Optical Instruments ────────────────────
            ('9001.10.00', 'Optical fibres and optical fibre bundles; cables', 0.00, 'unit', '90'),
            ('9003.11.00', 'Frames and mountings for spectacles, of plastics', 0.00, 'unit', '90'),
            ('9004.10.00', 'Sunglasses', 7.00, 'unit', '90'),
            ('9018.11.00', 'Electro-cardiographs (ECG)', 0.00, 'unit', '90'),
            ('9018.19.00', 'Other electro-diagnostic apparatus', 0.00, 'unit', '90'),
            ('9018.39.00', 'Needles, catheters, cannulae and the like, used in medical science', 0.00, 'unit', '90'),
            ('9019.20.00', 'Ozone therapy, oxygen therapy, aerosol therapy apparatus', 0.00, 'unit', '90'),
            ('9020.00.00', 'Other breathing appliances and gas masks', 0.00, 'unit', '90'),
            ('9021.10.00', 'Orthopaedic or fracture appliances (splints, braces)', 0.00, 'unit', '90'),
            ('9021.31.00', 'Artificial joints for implanting', 0.00, 'unit', '90'),
            ('9025.11.00', 'Thermometers, not combined with other instruments, liquid-filled', 1.00, 'unit', '90'),
            ('9027.80.90', 'Other instruments and apparatus for measuring/checking, other', 1.00, 'unit', '90'),

            # ── Chapter 91-92 — Clocks, Musical Instruments ───────────────────
            ('9102.11.00', 'Wrist-watches, electrically operated, with mechanical display only', 10.00, 'unit', '91'),
            ('9102.12.00', 'Wrist-watches, electrically operated, with opto-electronic display only', 10.00, 'unit', '91'),
            ('9205.90.00', 'Musical wind instruments (trumpets, clarinets, flutes, other)', 5.00, 'unit', '92'),
            ('9206.00.00', 'Percussion musical instruments (drums, xylophones, etc.)', 5.00, 'unit', '92'),

            # ── Chapter 94 — Furniture ────────────────────────────────────────
            ('9401.30.00', 'Swivel seats with variable height adjustment', 10.00, 'unit', '94'),
            ('9401.61.00', 'Seats with wooden frames, upholstered (other than garden/camp)', 10.00, 'unit', '94'),
            ('9401.80.00', 'Other seats (plastic chairs, metal chairs, etc.)', 10.00, 'unit', '94'),
            ('9403.10.00', 'Metal furniture of a kind used in offices', 10.00, 'unit', '94'),
            ('9403.20.00', 'Other metal furniture', 10.00, 'unit', '94'),
            ('9403.30.00', 'Wooden furniture of a kind used in offices', 10.00, 'unit', '94'),
            ('9403.40.00', 'Wooden furniture of a kind used in the kitchen', 10.00, 'unit', '94'),
            ('9403.60.00', 'Wooden furniture of a kind used in bedrooms', 10.00, 'unit', '94'),
            ('9404.21.00', 'Mattresses of cellular rubber or plastics', 10.00, 'unit', '94'),
            ('9404.29.00', 'Mattresses of other materials', 10.00, 'unit', '94'),

            # ── Chapter 95 — Toys, Games, Sports Equipment ────────────────────
            ('9503.00.00', 'Tricycles, scooters, pedal cars and other toys; dolls; puzzles; video game consoles', 5.00, 'unit', '95'),
            ('9504.50.00', 'Video game consoles and machines (other than from 9504.30)', 5.00, 'unit', '95'),
            ('9506.11.00', 'Ski and snowboard equipment', 10.00, 'unit', '95'),
            ('9506.62.00', 'Inflatable balls (basketballs, footballs, volleyballs)', 10.00, 'unit', '95'),
            ('9506.91.00', 'Articles and equipment for gymnastics, athletics, fitness', 10.00, 'unit', '95'),
            ('9507.10.00', 'Fishing rods', 10.00, 'unit', '95'),
            ('9507.30.00', 'Fishing reels', 10.00, 'unit', '95'),

            # ── Chapter 96 — Miscellaneous ────────────────────────────────────
            ('9601.10.00', 'Worked ivory and articles of ivory', 0.00, 'kg', '96'),
            ('9608.10.00', 'Ball point pens', 5.00, 'unit', '96'),
            ('9608.20.00', 'Felt-tipped and other porous-tipped pens and markers', 5.00, 'unit', '96'),
            ('9608.31.00', 'Indian ink drawing pens', 5.00, 'unit', '96'),
            ('9610.00.00', 'Slates and boards with writing or drawing surfaces', 5.00, 'unit', '96'),
            ('9616.10.00', 'Scent sprayers and similar toilet sprayers', 5.00, 'unit', '96'),
            ('9619.00.00', 'Sanitary towels, tampons, napkins and napkin liners', 5.00, 'kg', '96'),
        ]

        created = 0
        skipped = 0
        updated = 0

        for code, desc, rate, unit, chapter in hs_codes:
            obj, was_created = HSCode.objects.get_or_create(
                code=code,
                defaults={
                    'description': desc,
                    'duty_rate':   rate,
                    'unit':        unit,
                    'chapter':     chapter,
                    'is_active':   True,
                }
            )
            if was_created:
                created += 1
            else:
                # Update existing if description or rate changed
                changed = False
                if obj.description != desc:
                    obj.description = desc
                    changed = True
                if float(obj.duty_rate) != rate:
                    obj.duty_rate = rate
                    changed = True
                if obj.chapter != chapter:
                    obj.chapter = chapter
                    changed = True
                if changed:
                    obj.save()
                    updated += 1
                else:
                    skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Done! Created: {created}, Updated: {updated}, Skipped (no change): {skipped}'
            )
        )
