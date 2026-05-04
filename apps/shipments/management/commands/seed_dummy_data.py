"""
Seed command: python manage.py seed_dummy_data
Creates 10 consignees, 3 declarants, 1 supervisor, and 50 shipments
with computations, advisories, notifications, and status logs.
"""
import json
import random
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from apps.accounts.models import User
from apps.shipments.models import Shipment, HSCode, StatusLog
from apps.computation.models import DutyComputation, ShippingAdvisory
from apps.notifications.models import Notification


CONSIGNEES = [
    ('consignee01', 'Maria',    'Santos',     'maria.santos@demo.ph',     '+63-917-100-0001'),
    ('consignee02', 'Jose',     'Reyes',      'jose.reyes@demo.ph',       '+63-917-100-0002'),
    ('consignee03', 'Ana',      'Cruz',       'ana.cruz@demo.ph',         '+63-917-100-0003'),
    ('consignee04', 'Pedro',    'Garcia',     'pedro.garcia@demo.ph',     '+63-917-100-0004'),
    ('consignee05', 'Rosa',     'Martinez',   'rosa.martinez@demo.ph',    '+63-917-100-0005'),
    ('consignee06', 'Juan',     'Lopez',      'juan.lopez@demo.ph',       '+63-917-100-0006'),
    ('consignee07', 'Carmen',   'Hernandez',  'carmen.hernandez@demo.ph', '+63-917-100-0007'),
    ('consignee08', 'Miguel',   'Gonzalez',   'miguel.gonzalez@demo.ph',  '+63-917-100-0008'),
    ('consignee09', 'Liza',     'Flores',     'liza.flores@demo.ph',      '+63-917-100-0009'),
    ('consignee10', 'Roberto',  'Torres',     'roberto.torres@demo.ph',   '+63-917-100-0010'),
]

DECLARANTS = [
    ('declarant01', 'Elena',  'Bautista',   'elena.bautista@rtriplelj.ph',  '+63-917-200-0001'),
    ('declarant02', 'Marco',  'Dela Cruz',  'marco.delacruz@rtriplelj.ph',  '+63-917-200-0002'),
    ('declarant03', 'Sarah',  'Villanueva', 'sarah.villanueva@rtriplelj.ph', '+63-917-200-0003'),
]

SUPERVISORS = [
    ('supervisor01', 'Ricardo', 'Ramos', 'ricardo.ramos@rtriplelj.ph', '+63-917-300-0001'),
]

DESCRIPTIONS = [
    'Electronic components and PCB assemblies',
    'Industrial machinery spare parts',
    'Consumer electronics — tablets and accessories',
    'Medical devices and diagnostic equipment',
    'Automotive spare parts and accessories',
    'Textile fabrics and raw materials',
    'Food processing and packaging equipment',
    'Office furniture and ergonomic supplies',
    'Laboratory analytical instruments',
    'Cosmetics, skincare, and personal care products',
    'Pharmaceutical raw materials and APIs',
    'Optical instruments — cameras and lenses',
    'Electrical wiring harnesses and cables',
    'Computer hardware, servers, and peripherals',
    'Sports equipment, gym machines, and accessories',
    'Solar panels and renewable energy components',
    'CCTV cameras and security systems',
    'Printing equipment and consumables',
    'Agricultural machinery and irrigation systems',
    'Chemical reagents and laboratory supplies',
]

IMPORT_TYPES = ['permanent', 'repair', 'sample']
SHIP_TYPES   = ['lcl', 'fcl', 'air']
URGENCIES    = ['normal', 'urgent']


def rand_hawb(prefix, n):
    return f'{prefix}-{str(n).zfill(5)}'


def make_brokerage_fee(tv):
    tv = float(tv)
    if tv <= 10000:   return Decimal('1300')
    if tv <= 20000:   return Decimal('2000')
    if tv <= 30000:   return Decimal('2700')
    if tv <= 40000:   return Decimal('3300')
    if tv <= 50000:   return Decimal('3600')
    if tv <= 60000:   return Decimal('4000')
    if tv <= 100000:  return Decimal('4700')
    if tv <= 200000:  return Decimal('5300')
    return Decimal('6000')


def make_ipf(tv):
    tv = float(tv)
    if tv <= 25000:  return Decimal('250')
    if tv <= 50000:  return Decimal('500')
    if tv <= 250000: return Decimal('750')
    if tv <= 500000: return Decimal('1000')
    if tv <= 750000: return Decimal('1500')
    return Decimal('2000')


class Command(BaseCommand):
    help = 'Seed demo users, shipments, computations, and notifications'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear', action='store_true',
            help='Delete all existing seed data before seeding'
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options['clear']:
            self.stdout.write('Clearing existing seed data...')
            User.objects.filter(username__startswith='consignee').delete()
            User.objects.filter(username__startswith='declarant').delete()
            User.objects.filter(username__startswith='supervisor').delete()
            self.stdout.write(self.style.WARNING('Seed users deleted.'))

        # ── Create Users ──────────────────────────────────────────────────────
        consignees  = []
        declarants  = []
        supervisors = []

        for uname, fn, ln, email, phone in CONSIGNEES:
            u, created = User.objects.get_or_create(
                username=uname,
                defaults=dict(
                    first_name=fn, last_name=ln, email=email,
                    role='consignee', phone_number=phone, is_active=True,
                ),
            )
            if created:
                u.set_password('Demo@1234')
                u.save()
            consignees.append(u)

        for uname, fn, ln, email, phone in DECLARANTS:
            u, created = User.objects.get_or_create(
                username=uname,
                defaults=dict(
                    first_name=fn, last_name=ln, email=email,
                    role='declarant', phone_number=phone, is_active=True,
                ),
            )
            if created:
                u.set_password('Demo@1234')
                u.save()
            declarants.append(u)

        for uname, fn, ln, email, phone in SUPERVISORS:
            u, created = User.objects.get_or_create(
                username=uname,
                defaults=dict(
                    first_name=fn, last_name=ln, email=email,
                    role='supervisor', phone_number=phone, is_active=True,
                ),
            )
            if created:
                u.set_password('Demo@1234')
                u.save()
            supervisors.append(u)

        self.stdout.write(self.style.SUCCESS(
            f'Users ready — {len(consignees)} consignees, '
            f'{len(declarants)} declarants, {len(supervisors)} supervisor'
        ))

        # ── HS Codes ──────────────────────────────────────────────────────────
        hs_list = list(HSCode.objects.filter(is_active=True))
        if not hs_list:
            self.stdout.write(self.style.WARNING(
                'No HS codes found. Run your HS code seed first. Skipping computations.'
            ))

        # ── Shipment distribution ─────────────────────────────────────────────
        # 50 total: 15 pending, 10 in_review, 8 for_payment, 7 submitted, 6 approved, 4 rejected
        status_plan = (
            ['pending']     * 15 +
            ['in_review']   * 10 +
            ['for_payment'] * 8  +
            ['submitted']   * 7  +
            ['approved']    * 6  +
            ['rejected']    * 4
        )
        random.shuffle(status_plan)

        shipments_created = 0
        for i, status in enumerate(status_plan, start=1):
            hawb = rand_hawb('DEMO', i)
            if Shipment.objects.filter(hawb_number=hawb).exists():
                continue

            consignee = random.choice(consignees)
            desc      = random.choice(DESCRIPTIONS)
            itype     = random.choice(IMPORT_TYPES)
            stype     = random.choice(SHIP_TYPES)
            urgency   = random.choices(URGENCIES, weights=[70, 30])[0]
            qty       = Decimal(str(random.randint(1, 500)))
            weight    = Decimal(str(round(random.uniform(0.5, 2000), 2)))
            exw_usd   = Decimal(str(round(random.uniform(200, 50000), 2)))
            freight   = Decimal(str(round(random.uniform(50, 3000), 2)))
            insurance = Decimal(str(round(exw_usd * Decimal('0.005'), 2)))

            declarant = None
            if status not in ('pending',):
                declarant = random.choice(declarants)

            shipment = Shipment.objects.create(
                hawb_number=hawb,
                consignee=consignee,
                declarant=declarant,
                import_type=itype,
                shipment_type=stype,
                urgency=urgency,
                status=status,
                description=desc,
                quantity=qty,
                gross_weight=weight,
                declared_value=exw_usd,
                freight_cost=freight,
                insurance_cost=insurance,
                boc_reference=f'BOC-{i:05d}' if status in ('submitted', 'approved', 'rejected') else None,
                boc_status=(
                    'Accepted' if status == 'approved' else
                    'Rejected' if status == 'rejected' else
                    ('Under Assessment' if status == 'submitted' else None)
                ),
            )

            # Status log
            StatusLog.objects.create(
                shipment=shipment,
                changed_by=declarant or consignee,
                old_status='pending',
                new_status=status,
                notes='Seeded record',
            )

            # Computation for in_review and beyond
            if status not in ('pending',) and hs_list and declarant:
                hs = random.choice(hs_list)
                exchange_rate = Decimal('59.1480')
                duty_rate = hs.duty_rate
                other_charges = exw_usd * Decimal('0.03')
                dv_usd = exw_usd + freight + insurance + other_charges
                dv_php = dv_usd * exchange_rate
                cud = dv_php * (duty_rate / Decimal('100'))
                taxable_value = round(dv_php, 2)
                customs_duties = round(cud, 2)
                vat_base = taxable_value + customs_duties
                vat = round(vat_base * Decimal('0.12'), 2)
                bf = make_brokerage_fee(taxable_value)
                ipf = make_ipf(taxable_value)
                tlc = round(taxable_value + customs_duties + vat + bf + Decimal('130') + ipf, 2)

                items = [{
                    'no': 1,
                    'description': desc,
                    'quantity': str(qty),
                    'exw': float(exw_usd),
                    'item_freight': float(freight),
                    'item_insurance': float(insurance),
                    'other_charges': float(other_charges),
                    'dv_usd': float(dv_usd),
                    'dv_php': float(dv_php),
                    'cud': float(cud),
                }]

                DutyComputation.objects.create(
                    shipment=shipment,
                    hs_code=hs,
                    total_freight=freight,
                    total_insurance=insurance,
                    exchange_rate=exchange_rate,
                    duty_rate=duty_rate,
                    declared_value=exw_usd,
                    items_json=json.dumps(items),
                    dutiable_value=taxable_value,
                    customs_duty=customs_duties,
                    vat_base=vat_base,
                    vat_amount=vat,
                    brokerage_fee=bf,
                    ipf=ipf,
                    total_landed_cost=tlc,
                    computed_by=declarant,
                )

                # Shipping advisory
                ShippingAdvisory.objects.create(
                    shipment=shipment,
                    gross_weight=weight,
                    cargo_volume=round(weight / Decimal('300'), 2),
                    declared_value=exw_usd,
                    urgency_level=urgency,
                    distance_km=Decimal(str(random.randint(500, 8000))),
                    lcl_score=Decimal(str(round(random.uniform(0.4, 0.8), 4))),
                    fcl_score=Decimal(str(round(random.uniform(0.4, 0.8), 4))),
                    air_score=Decimal(str(round(random.uniform(0.4, 0.8), 4))),
                    recommended_type=stype,
                    computed_by=declarant,
                )

                # Notify consignee computation is ready
                Notification.objects.create(
                    recipient=consignee,
                    shipment=shipment,
                    notification_type='computation',
                    title=f'Computation Ready — {hawb}',
                    message=f'Estimated Total Landed Cost: ₱{tlc:,.2f}',
                )

            # Notify consignee of status
            if status in ('approved', 'rejected'):
                Notification.objects.create(
                    recipient=consignee,
                    shipment=shipment,
                    notification_type=status,
                    title=f'Shipment {status.title()} — {hawb}',
                    message=(
                        f'Your shipment {hawb} has been {status} by the Bureau of Customs.'
                    ),
                )

            shipments_created += 1

        self.stdout.write(self.style.SUCCESS(
            f'Seeded {shipments_created} shipments.'
        ))
        self.stdout.write('')
        self.stdout.write('─' * 50)
        self.stdout.write('Demo credentials (all passwords: Demo@1234)')
        self.stdout.write('  Consignees : consignee01 … consignee10')
        self.stdout.write('  Declarants : declarant01, declarant02, declarant03')
        self.stdout.write('  Supervisor : supervisor01')
        self.stdout.write('─' * 50)
