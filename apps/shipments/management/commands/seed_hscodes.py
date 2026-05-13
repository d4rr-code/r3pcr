from django.core.management.base import BaseCommand
from apps.shipments.models import HSCode


class Command(BaseCommand):
    help = 'Seed HS Codes based on the Philippine AHTN 2022 Tariff Book'

    def handle(self, *args, **kwargs):
        # Format: (code, description, duty_rate %, unit, chapter)
        # Duty rates follow Philippine MFN rates under CMTA / EO 82 s.2019 / AHTN 2022
        hs_codes = [

            # ── Chapter 84 — Machinery & Mechanical Appliances ────────────────
            ('8471.30.00', 'Portable automatic data processing machines (laptops/notebooks, ≤10 kg)', 0.00, 'unit', '84'),
            ('8471.41.00', 'Other automatic data processing machines (desktop computers)', 0.00, 'unit', '84'),
            ('8471.49.00', 'Other data processing systems (servers, workstations)', 0.00, 'unit', '84'),
            ('8471.60.00', 'Input/output units for ADP machines (keyboards, mice, scanners)', 0.00, 'unit', '84'),
            ('8473.30.00', 'Parts and accessories for computers (RAM, HDD, SSD, etc.)', 0.00, 'unit', '84'),
            ('8415.10.00', 'Air conditioning machines, window/wall type', 5.00, 'unit', '84'),
            ('8418.10.00', 'Combined refrigerator-freezers', 5.00, 'unit', '84'),
            ('8419.89.00', 'Machinery for treating material by temperature change (industrial)', 3.00, 'unit', '84'),
            ('8422.30.00', 'Machinery for filling, closing, sealing bottles/cans', 5.00, 'unit', '84'),
            ('8450.11.00', 'Fully-automatic household washing machines (≤10 kg)', 5.00, 'unit', '84'),
            ('8452.10.00', 'Sewing machines, household type', 5.00, 'unit', '84'),
            ('8467.11.00', 'Rotary type pneumatic tools (drills, sanders)', 5.00, 'unit', '84'),
            ('8481.80.00', 'Taps, cocks, valves and similar appliances', 5.00, 'unit', '84'),
            ('8483.40.00', 'Gears and gearing; ball and roller screws; gear boxes', 3.00, 'unit', '84'),

            # ── Chapter 85 — Electrical Machinery & Equipment ─────────────────
            ('8517.12.00', 'Telephones for cellular networks, smartphones', 0.00, 'unit', '85'),
            ('8517.62.00', 'Machines for the reception, conversion and transmission of data', 0.00, 'unit', '85'),
            ('8518.21.00', 'Single loudspeakers, mounted in their enclosures', 3.00, 'unit', '85'),
            ('8518.30.00', 'Headphones, earphones, and combined microphone/speaker sets', 3.00, 'unit', '85'),
            ('8518.40.00', 'Audio-frequency electric amplifiers', 3.00, 'unit', '85'),
            ('8521.10.00', 'Magnetic tape-type video recording/reproducing apparatus', 3.00, 'unit', '85'),
            ('8523.51.00', 'Solid-state non-volatile storage devices (USB flash drives, memory cards)', 0.00, 'unit', '85'),
            ('8525.60.00', 'Transmission apparatus for radio-broadcasting / TV (cameras)', 0.00, 'unit', '85'),
            ('8528.72.00', 'Television receivers, color, capable of receiving analog/digital signals', 5.00, 'unit', '85'),
            ('8534.00.00', 'Printed circuits', 0.00, 'unit', '85'),
            ('8536.50.00', 'Switches, other than relays (≤1000 V)', 3.00, 'unit', '85'),
            ('8544.42.00', 'Electric conductors/wires, fitted with connectors (voltage ≤1000 V)', 3.00, 'unit', '85'),

            # ── Chapter 87 — Vehicles ─────────────────────────────────────────
            ('8703.23.19', 'Motor cars, spark-ignition, 1500–3000 cc (gasoline, not CKD)', 30.00, 'unit', '87'),
            ('8703.40.91', 'Motor cars, plug-in hybrid electric, not CKD', 5.00, 'unit', '87'),
            ('8703.80.91', 'Motor cars, battery electric, not CKD', 5.00, 'unit', '87'),
            ('8708.29.90', 'Parts and accessories of motor vehicles (body parts, bumpers)', 5.00, 'unit', '87'),
            ('8708.99.90', 'Other parts and accessories of motor vehicles', 5.00, 'unit', '87'),
            ('8712.00.00', 'Bicycles and other cycles, non-motorized', 10.00, 'unit', '87'),
            ('8714.99.90', 'Parts and accessories for cycles (other)', 10.00, 'unit', '87'),

            # ── Chapter 61-62 — Clothing & Garments ──────────────────────────
            ('6109.10.00', 'T-shirts, singlets and other vests, of cotton, knitted', 10.00, 'kg', '61'),
            ('6109.90.00', 'T-shirts, singlets and other vests, of other textile material', 10.00, 'kg', '61'),
            ('6201.12.00', 'Overcoats, car coats of cotton, for men/boys', 10.00, 'kg', '62'),
            ('6203.42.00', 'Men\'s trousers and shorts of cotton, not knitted', 10.00, 'kg', '62'),
            ('6204.62.00', 'Women\'s trousers and shorts of cotton, not knitted', 10.00, 'kg', '62'),
            ('6211.43.00', 'Men\'s garments of man-made fibers (tracksuit, etc.)', 10.00, 'kg', '62'),
            ('6217.10.00', 'Accessories for clothing (scarves, ties, handkerchiefs, etc.)', 10.00, 'kg', '62'),

            # ── Chapter 64 — Footwear ─────────────────────────────────────────
            ('6401.92.00', 'Waterproof footwear with rubber/plastic outer soles, covering ankle only', 10.00, 'pair', '64'),
            ('6403.99.00', 'Footwear with leather uppers, other (not covering ankle)', 10.00, 'pair', '64'),
            ('6404.11.00', 'Footwear with rubber/plastic outer soles, textile uppers (sports)', 10.00, 'pair', '64'),
            ('6405.20.00', 'Footwear with textile uppers, other', 10.00, 'pair', '64'),

            # ── Chapter 01-24 — Food & Agricultural Products ──────────────────
            ('0901.11.10', 'Coffee, not roasted, not decaffeinated', 5.00, 'kg', '09'),
            ('1006.30.10', 'Semi-milled or wholly milled rice, for human consumption', 35.00, 'kg', '10'),
            ('1901.90.99', 'Food preparations of flour, groats, starch or malt extract (other)', 7.00, 'kg', '19'),
            ('2101.11.00', 'Extracts, essences and concentrates of coffee', 7.00, 'kg', '21'),
            ('2101.20.00', 'Extracts, essences and concentrates of tea or maté', 7.00, 'kg', '21'),
            ('1704.90.00', 'Sugar confectionery not containing cocoa (candies, chewing gum)', 7.00, 'kg', '17'),
            ('1806.90.00', 'Chocolate and other food preparations containing cocoa (other)', 7.00, 'kg', '18'),
            ('2009.89.90', 'Juice of other fruit or vegetables, unfermented (other)', 7.00, 'L',  '20'),
            ('2202.10.00', 'Waters, including mineral waters and aerated waters, sweetened', 7.00, 'L',  '22'),
            ('2106.90.99', 'Food preparations not elsewhere specified (protein supplements, etc.)', 7.00, 'kg', '21'),

            # ── Chapter 30 & 33 — Medical / Cosmetics ─────────────────────────
            ('3004.50.00', 'Medicaments containing vitamins or provitamins', 0.00, 'kg', '30'),
            ('3004.90.90', 'Medicaments for therapeutic or prophylactic use, other', 0.00, 'kg', '30'),
            ('3005.10.00', 'Adhesive dressings and other articles having an adhesive layer (bandages)', 0.00, 'kg', '30'),
            ('3303.00.00', 'Perfumes and toilet waters', 10.00, 'kg', '33'),
            ('3304.10.00', 'Lip make-up preparations', 10.00, 'kg', '33'),
            ('3304.20.00', 'Eye make-up preparations', 10.00, 'kg', '33'),
            ('3304.99.00', 'Beauty or make-up preparations; skin-care preparations (other)', 10.00, 'kg', '33'),
            ('3305.10.00', 'Shampoos', 10.00, 'L',  '33'),
            ('3306.10.00', 'Dentifrices (toothpaste, tooth powder)', 10.00, 'kg', '33'),
            ('3307.20.00', 'Personal deodorants and antiperspirants', 10.00, 'kg', '33'),

            # ── Chapter 39 — Plastics ─────────────────────────────────────────
            ('3917.32.00', 'Other flexible tubes, pipes and hoses of plastics', 3.00, 'kg', '39'),
            ('3919.90.00', 'Self-adhesive plates, sheets and film of plastics (other)', 3.00, 'kg', '39'),
            ('3926.90.99', 'Other articles of plastics (other)', 3.00, 'kg', '39'),

            # ── Chapter 40 — Rubber ───────────────────────────────────────────
            ('4011.10.00', 'New pneumatic tyres of rubber for motor cars', 3.00, 'unit', '40'),
            ('4011.20.00', 'New pneumatic tyres of rubber for buses or lorries', 3.00, 'unit', '40'),
            ('4016.99.90', 'Other articles of vulcanized rubber (other)', 3.00, 'kg', '40'),

            # ── Chapter 49 — Printed Matter ───────────────────────────────────
            ('4901.99.00', 'Other printed books, brochures, leaflets and similar', 0.00, 'kg', '49'),
            ('4902.90.00', 'Newspapers, journals and periodicals (other)', 0.00, 'kg', '49'),
            ('4911.10.00', 'Trade advertising material, commercial catalogues', 0.00, 'kg', '49'),

            # ── Chapter 72-73 — Steel & Iron ──────────────────────────────────
            ('7208.51.00', 'Flat-rolled products of iron or non-alloy steel (thickness > 10 mm)', 3.00, 'kg', '72'),
            ('7209.16.00', 'Flat-rolled products of iron or steel, cold-rolled (0.5–1 mm)', 3.00, 'kg', '72'),
            ('7214.20.00', 'Bars/rods of iron or non-alloy steel, twisted / with indentations', 3.00, 'kg', '72'),
            ('7304.31.00', 'Cold-drawn or cold-rolled seamless tubes of circular cross-section', 3.00, 'kg', '73'),

            # ── Chapter 83 — Miscellaneous Articles of Base Metal ─────────────
            ('8301.40.00', 'Locks of a kind used on furniture, luggage, etc.', 5.00, 'unit', '83'),
            ('8302.42.00', 'Mountings, fittings for furniture (hinges, handles)', 5.00, 'unit', '83'),
            ('8306.21.00', 'Statuettes and ornaments of base metal, silver-plated', 5.00, 'unit', '83'),

            # ── Chapter 90 — Medical Instruments & Apparatus ──────────────────
            ('9018.11.00', 'Electro-cardiographs', 0.00, 'unit', '90'),
            ('9018.39.00', 'Needles, catheters, cannulae and the like, used in medical science', 0.00, 'unit', '90'),
            ('9019.20.00', 'Ozone therapy, oxygen therapy, aerosol therapy apparatus', 0.00, 'unit', '90'),
            ('9021.10.00', 'Orthopaedic or fracture appliances (splints, braces)', 0.00, 'unit', '90'),
            ('9025.11.00', 'Thermometers, not combined with other instruments, liquid-filled', 1.00, 'unit', '90'),
            ('9027.80.90', 'Other instruments and apparatus for measuring / checking, other', 1.00, 'unit', '90'),

            # ── Chapter 94 — Furniture ────────────────────────────────────────
            ('9401.61.00', 'Seats with wooden frames, upholstered (other than garden/camp)', 10.00, 'unit', '94'),
            ('9403.30.00', 'Wooden furniture of a kind used in offices', 10.00, 'unit', '94'),
            ('9403.60.00', 'Wooden furniture of a kind used in bedrooms', 10.00, 'unit', '94'),
            ('9404.21.00', 'Mattresses of cellular rubber or plastics', 10.00, 'unit', '94'),

            # ── Chapter 95 — Toys, Games, Sports Equipment ────────────────────
            ('9503.00.00', 'Tricycles, scooters, pedal cars and other toys; dolls; video game consoles', 5.00, 'unit', '95'),
            ('9506.62.00', 'Inflatable balls (basketballs, footballs, volleyballs)', 10.00, 'unit', '95'),
            ('9506.91.00', 'Articles and equipment for gymnastics, athletics, fitness', 10.00, 'unit', '95'),
            ('9507.10.00', 'Fishing rods', 10.00, 'unit', '95'),

            # ── Chapter 96 — Miscellaneous ────────────────────────────────────
            ('9608.10.00', 'Ball point pens', 5.00, 'unit', '96'),
            ('9616.10.00', 'Scent sprayers and similar toilet sprayers', 5.00, 'unit', '96'),
        ]

        created = 0
        skipped = 0

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
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'✅ Done! Created: {created}, Skipped (already exist): {skipped}'
            )
        )
