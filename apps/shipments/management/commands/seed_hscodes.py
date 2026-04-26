from django.core.management.base import BaseCommand
from apps.shipments.models import HSCode

class Command(BaseCommand):
    help = 'Seed HS Codes into the database'

    def handle(self, *args, **kwargs):
        hs_codes = [
            # Electronics
            ('8471.30.00', 'Portable automatic data processing machines (laptops)', 0.00, 'unit', '84'),
            ('8471.41.00', 'Other automatic data processing machines (desktop computers)', 0.00, 'unit', '84'),
            ('8517.12.00', 'Telephones for cellular networks (mobile phones)', 0.00, 'unit', '85'),
            ('8528.72.00', 'Television receivers, color', 3.00, 'unit', '85'),
            ('8518.30.00', 'Headphones and earphones', 3.00, 'unit', '85'),
            ('8543.70.00', 'Electric machines and apparatus (electronic devices)', 3.00, 'unit', '85'),

            # Machinery & Equipment
            ('8419.89.00', 'Machinery for treating materials by temperature change', 5.00, 'unit', '84'),
            ('8422.30.00', 'Machinery for filling, closing, sealing containers', 5.00, 'unit', '84'),
            ('8481.80.00', 'Taps, cocks, valves and similar appliances', 5.00, 'unit', '84'),

            # Clothing & Textiles
            ('6109.10.00', 'T-shirts, singlets of cotton', 10.00, 'kg', '61'),
            ('6203.42.00', 'Men\'s trousers and shorts of cotton', 10.00, 'kg', '62'),
            ('6204.62.00', 'Women\'s trousers and shorts of cotton', 10.00, 'kg', '62'),
            ('6401.92.00', 'Waterproof footwear with rubber soles', 10.00, 'pair', '64'),

            # Food & Beverages
            ('1901.90.00', 'Food preparations of flour, starch or milk', 7.00, 'kg', '19'),
            ('2101.11.00', 'Extracts, essences and concentrates of coffee', 7.00, 'kg', '21'),
            ('1704.90.00', 'Sugar confectionery not containing cocoa', 7.00, 'kg', '17'),

            # Cosmetics & Personal Care
            ('3304.99.00', 'Beauty or make-up preparations', 10.00, 'kg', '33'),
            ('3305.10.00', 'Shampoos', 10.00, 'L', '33'),
            ('3307.41.00', 'Agarbatti and other odoriferous preparations', 10.00, 'kg', '33'),

            # Automotive Parts
            ('8708.29.00', 'Parts and accessories of motor vehicles', 5.00, 'unit', '87'),
            ('8712.00.00', 'Bicycles and other cycles', 5.00, 'unit', '87'),
            ('4011.10.00', 'New pneumatic tyres of rubber for motor cars', 5.00, 'unit', '40'),

            # Medical Supplies
            ('9018.39.00', 'Needles, catheters, cannulae and medical instruments', 0.00, 'unit', '90'),
            ('3004.90.00', 'Medicaments for therapeutic or prophylactic use', 0.00, 'kg', '30'),
            ('9019.20.00', 'Ozone therapy and oxygen therapy apparatus', 0.00, 'unit', '90'),

            # Industrial Goods
            ('7208.51.00', 'Flat-rolled products of iron or non-alloy steel', 3.00, 'kg', '72'),
            ('3917.32.00', 'Other tubes, pipes and hoses of plastics', 3.00, 'kg', '39'),
            ('8301.40.00', 'Locks of a kind used for furniture', 5.00, 'unit', '83'),

            # Sports & Recreation
            ('9506.62.00', 'Inflatable balls', 10.00, 'unit', '95'),
            ('9506.91.00', 'Articles and equipment for general physical exercise', 10.00, 'unit', '95'),

            # Books & Printed Materials
            ('4901.99.00', 'Other printed books, brochures, leaflets', 0.00, 'kg', '49'),
            ('4911.10.00', 'Trade advertising material, commercial catalogues', 0.00, 'kg', '49'),
        ]

        created = 0
        skipped = 0

        for code, desc, rate, unit, chapter in hs_codes:
            obj, was_created = HSCode.objects.get_or_create(
                code=code,
                defaults={
                    'description': desc,
                    'duty_rate': rate,
                    'unit': unit,
                    'chapter': chapter,
                    'is_active': True,
                }
            )
            if was_created:
                created += 1
            else:
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'✅ Done! Created: {created}, Skipped: {skipped}'
            )
        )