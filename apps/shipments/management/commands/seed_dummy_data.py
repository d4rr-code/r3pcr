"""
Seed command: python manage.py seed_dummy_data [--count 420] [--months 96] [--clear]

Generates realistic, historically-spread demo shipments so the analytics
dashboards show genuine trends and patterns (not a single flat bucket).

Key behaviours:
  * Shipments are spread across the last N months (default 96) with trend and
    seasonality, so ARIMA forecasts have usable demo history.
  * Completed historical shipments are guaranteed for recent historical years
    by default, so yearly forecast charts have enough points for model comparison.
  * Every status in Shipment.STATUS_CHOICES is represented and randomised.
  * Each shipment gets a backdated status-log timeline (incoming -> ... -> final)
    so processing-time analytics have data to measure.
  * Computed+ shipments get a DutyComputation and a real MCDA ShippingAdvisory.
  * Billed shipments get consignee Feedback (mostly positive, some approved).
  * --clear removes only previously-seeded DEMO shipments. It never deletes
    user accounts or real shipments.
"""
import json
import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import connection, transaction

from apps.accounts.models import User
from apps.shipments.models import Shipment, ShipmentDocument, HSCode, ShipmentHSCode, StatusLog
from apps.computation.models import DutyComputation, ShippingAdvisory, ShipmentLineItem
from apps.consignee.models import Feedback
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
    ('declarant01', 'Elena',  'Bautista',   'elena.bautista@rtriplelj.ph',   '+63-917-200-0001'),
    ('declarant02', 'Marco',  'Dela Cruz',  'marco.delacruz@rtriplelj.ph',   '+63-917-200-0002'),
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

IMPORT_TYPES = ['commercial', 'personal', 'balikbayan', 'samples',
                'machinery', 'raw_materials', 'ecommerce']
SHIP_TYPES   = ['lcl', 'fcl', 'air']
SHIP_WEIGHTS = [40, 35, 25]
URGENCIES    = ['standard', 'priority', 'urgent', 'rush']
URG_WEIGHTS  = [40, 30, 20, 10]

# Invoice currency mix — ~85% USD, the rest spread across other supported
# currencies so the analytics currency breakdown isn't single-valued.
CURRENCIES   = ['USD', 'EUR', 'JPY', 'HKD', 'CNY', 'GBP', 'SGD']
CUR_WEIGHTS  = [85, 4, 3, 2, 2, 2, 2]

FEEDBACK_COMMENTS = [
    'Smooth clearance and clear cost breakdown. Will use again.',
    'Fast processing, kept us updated the whole way through.',
    'The landed-cost estimate matched the final bill almost exactly.',
    'Very professional handling. Documentation was thorough.',
    'Good service overall, a minor delay at assessment but well communicated.',
    'Transparent fees and responsive declarant. Highly recommended.',
    'Reliable as always. The advisory helped us pick the right mode.',
    'Clear updates at every status change. Appreciated the heads-up emails.',
    'Quick turnaround from arrival to release. Great job.',
    'Helpful team, accurate computation, no surprises on the final cost.',
]

# Linear happy-path pipeline. Branch statuses (rejected / for_revision) are
# handled separately in _build_path().
PIPELINE = ['incoming', 'arrived', 'computed', 'approved',
            'lodgement', 'ongoing', 'assessed', 'paid', 'released', 'billed']

# Relative likelihood of each *final* status. Billed is weighted up so history,
# feedback and completion analytics have plenty to show.
FINAL_WEIGHTS = {
    'incoming':     8,
    'arrived':      8,
    'computed':    10,
    'for_revision': 6,
    'rejected':     6,
    'approved':     8,
    'lodgement':    8,
    'ongoing':      8,
    'assessed':     8,
    'paid':         8,
    'released':     8,
    'billed':      14,
}


# Demo shipments use the real R3PCR-{year}-{seq} numbering, continuing
# seamlessly from genuine shipments so the references look authentic. Because
# the HAWB no longer distinguishes them, each seed shipment is tagged with a
# sentinel note on its status logs — a string real shipments never produce —
# so --clear can target them safely without a schema change.
SEED_NOTE = '[seed:r3pcr-demo]'
DEFAULT_HISTORICAL_YEARS = (2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025)


def _seed_ids():
    return list(
        Shipment.objects.filter(status_logs__notes=SEED_NOTE)
        .distinct().values_list('id', flat=True)
    )


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


def _build_path(final):
    """Return the ordered list of statuses a shipment passed through."""
    if final == 'incoming':
        return ['incoming']
    if final == 'rejected':
        # Rejected either at arrival or after computation.
        return random.choice([
            ['incoming', 'arrived', 'rejected'],
            ['incoming', 'arrived', 'computed', 'rejected'],
        ])
    if final == 'for_revision':
        return ['incoming', 'arrived', 'computed', 'for_revision']
    return PIPELINE[:PIPELINE.index(final) + 1]


def _add_months(value, months):
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return value.replace(year=year, month=month, day=1)


def _parse_years(value):
    years = []
    for part in str(value or '').split(','):
        part = part.strip()
        if not part:
            continue
        try:
            year = int(part)
        except ValueError:
            continue
        if 2000 <= year <= 2100 and year not in years:
            years.append(year)
    return years


class Command(BaseCommand):
    help = 'Seed historically-spread demo shipments for analytics dashboards'

    def add_arguments(self, parser):
        parser.add_argument('--count',  type=int, default=420,
                            help='Number of shipments to create (default 420)')
        parser.add_argument('--months', type=int, default=96,
                            help='Spread submissions across the last N months (default 96)')
        parser.add_argument('--historical-years', default=','.join(str(y) for y in DEFAULT_HISTORICAL_YEARS),
                            help='Comma-separated years that must receive completed demo shipments (default 2022,2023)')
        parser.add_argument('--historical-year-count', type=int, default=30,
                            help='Minimum completed demo shipments to reserve for each historical year (default 30)')
        parser.add_argument('--clear',  action='store_true',
                            help='Delete previously-seeded DEMO shipments first (keeps users)')

    @transaction.atomic
    def handle(self, *args, **options):
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = '10min'")

        count  = max(1, options['count'])
        months = max(1, options['months'])
        now    = timezone.now()
        historical_years = [year for year in _parse_years(options['historical_years']) if year <= now.year]
        historical_year_count = max(0, options['historical_year_count'])

        # ── Optional clear (sentinel-tagged demo shipments only — never users) ─
        if options['clear']:
            demo_ids = _seed_ids()
            n = len(demo_ids)
            Feedback.objects.filter(shipment_id__in=demo_ids).delete()
            Notification.objects.filter(shipment_id__in=demo_ids).delete()
            StatusLog.objects.filter(shipment_id__in=demo_ids).delete()
            ShipmentLineItem.objects.filter(shipment_id__in=demo_ids).delete()
            ShipmentHSCode.objects.filter(shipment_id__in=demo_ids).delete()
            DutyComputation.objects.filter(shipment_id__in=demo_ids).delete()
            ShippingAdvisory.objects.filter(shipment_id__in=demo_ids).delete()
            ShipmentDocument.objects.filter(shipment_id__in=demo_ids).delete()
            Shipment.objects.filter(id__in=demo_ids).delete()
            self.stdout.write(self.style.WARNING(f'Cleared {n} existing demo shipments.'))

        # ── Users ─────────────────────────────────────────────────────────────
        consignees, declarants, supervisors = [], [], []
        for bucket, rows, role in (
            (consignees, CONSIGNEES, 'consignee'),
            (declarants, DECLARANTS, 'declarant'),
            (supervisors, SUPERVISORS, 'supervisor'),
        ):
            for uname, fn, ln, email, phone in rows:
                u, created = User.objects.get_or_create(
                    username=uname,
                    defaults=dict(first_name=fn, last_name=ln, email=email,
                                  role=role, phone_number=phone, is_active=True),
                )
                if created:
                    u.set_password('Demo@1234')
                    u.save()
                bucket.append(u)

        self.stdout.write(self.style.SUCCESS(
            f'Users ready — {len(consignees)} consignees, '
            f'{len(declarants)} declarants, {len(supervisors)} supervisor'
        ))

        hs_list = list(HSCode.objects.filter(is_active=True))
        if not hs_list:
            self.stdout.write(self.style.WARNING(
                'No active HS codes found — run seed_hscodes first. '
                'Computations/advisories will be skipped.'
            ))

        # Real MCDA scorer (lazy import; fall back to random if unavailable).
        try:
            from apps.computation.views import compute_wmcda
        except Exception:
            compute_wmcda = None

        # ── Final-status plan: almost all 'billed', with a small spread of
        # other statuses for variety (each non-billed status appears >= once).
        statuses = list(FINAL_WEIGHTS.keys())
        other_statuses = [s for s in statuses if s != 'billed']
        plan = ['billed'] * count
        n_other = min(count, max(len(other_statuses), int(round(count * 0.15))))
        for i in range(n_other):
            plan[i] = other_statuses[i % len(other_statuses)]
        random.shuffle(plan)

        historical_overrides = []
        for year in historical_years:
            for _ in range(historical_year_count):
                submitted_at = now.replace(
                    year=year,
                    month=random.randint(1, 12),
                    day=random.randint(1, 28),
                    hour=random.randint(8, 17),
                    minute=random.randint(0, 59),
                    second=0,
                    microsecond=0,
                )
                if submitted_at <= now:
                    historical_overrides.append(submitted_at)
        random.shuffle(historical_overrides)
        historical_overrides = historical_overrides[:count]
        for i in range(len(historical_overrides)):
            plan[i] = random.choice(['released', 'billed'])

        # Per-year HAWB counters continuing seamlessly from the highest existing
        # genuine sequence, so demo references look just like real ones.
        year_counters = {}
        for h in (Shipment.objects.filter(hawb_number__startswith='R3PCR-')
                  .values_list('hawb_number', flat=True)):
            try:
                y, s = int(h.split('-')[1]), int(h.split('-')[2])
                year_counters[y] = max(year_counters.get(y, 0), s)
            except (IndexError, ValueError):
                pass

        exchange_rate = Decimal('58.50')
        created = 0
        status_tally = {s: 0 for s in statuses}

        for i, final in enumerate(plan):
            consignee = random.choice(consignees)
            desc      = random.choice(DESCRIPTIONS)
            itype     = random.choice(IMPORT_TYPES)
            stype     = random.choices(SHIP_TYPES, weights=SHIP_WEIGHTS)[0]
            urgency   = random.choices(URGENCIES, weights=URG_WEIGHTS)[0]
            currency  = random.choices(CURRENCIES, weights=CUR_WEIGHTS)[0]
            qty       = Decimal(str(random.randint(1, 500)))
            weight    = Decimal(str(round(random.uniform(0.5, 2000), 2)))
            volume    = Decimal(str(round(random.uniform(0.3, 28), 2)))
            exw_usd   = Decimal(str(round(random.uniform(200, 50000), 2)))
            freight   = Decimal(str(round(random.uniform(50, 3000), 2)))
            insurance = Decimal(str(round(exw_usd * Decimal('0.005'), 2)))
            distance  = random.randint(500, 12000)

            path = _build_path(final)
            reached_computed = 'computed' in path
            declarant = random.choice(declarants) if final != 'incoming' else None

            # Backdated submission date: trend + realistic import seasonality.
            month_weights = []
            for m in range(months):
                period = _add_months(now.date().replace(day=1), -m)
                trend = months - m
                seasonal = 1.0
                if period.month in (3, 5, 6, 10, 11):
                    seasonal += 0.45
                if period.month in (1, 2):
                    seasonal -= 0.20
                if period.month == 12:
                    seasonal += 0.25
                month_weights.append(max(1, int(trend * seasonal)))
            month_offset = random.choices(range(months), weights=month_weights)[0]
            days_ago = month_offset * 30 + random.randint(0, 29)
            submitted_at = now - timedelta(
                days=days_ago, hours=random.randint(0, 9), minutes=random.randint(0, 59)
            )
            if submitted_at > now:
                submitted_at = now - timedelta(hours=1)
            if i < len(historical_overrides):
                submitted_at = historical_overrides[i]

            # Active operational statuses should look like current work, not
            # months-old historical records. Keep history spread on released /
            # billed shipments, and keep active queues recent enough that
            # intelligence metrics do not imply unrealistic 100+ day workflow
            # averages in demo data.
            if final == 'incoming':
                submitted_at = now - timedelta(
                    days=random.randint(0, 2),
                    hours=random.randint(0, 9), minutes=random.randint(0, 59),
                )
            elif final not in ('released', 'billed'):
                submitted_at = now - timedelta(
                    days=random.randint(1, min(max(len(path) * 2, 3), 10)),
                    hours=random.randint(0, 9), minutes=random.randint(0, 59),
                )

            # HAWB in the real format, continuing the genuine per-year sequence.
            year = submitted_at.year
            seq = year_counters.get(year, 0) + 1
            hawb = f'R3PCR-{year}-{seq:06d}'
            while Shipment.objects.filter(hawb_number=hawb).exists():
                seq += 1
                hawb = f'R3PCR-{year}-{seq:06d}'
            year_counters[year] = seq

            shipment = Shipment.objects.create(
                hawb_number=hawb, consignee=consignee, declarant=declarant,
                import_type=itype, shipment_type=stype, urgency=urgency,
                status=final, description=desc, quantity=qty, gross_weight=weight,
                invoice_currency=currency, declared_value=exw_usd,
                freight_cost=freight, insurance_cost=insurance,
                estimated_arrival_date=(submitted_at + timedelta(days=random.randint(0, 2))).date(),
                container_number=f'DEMO{random.randint(1000000, 9999999)}' if stype == 'fcl' else '',
                job_order_reference=f'DEMO-JO-{year}-{seq:05d}',
            )

            doc_types = ['invoice', 'packing_list', 'airway_bill']
            if random.random() < 0.10 and final not in ('released', 'billed'):
                doc_types.pop(random.randrange(len(doc_types)))
                shipment.has_deficiency = True
                shipment.deficiency_type = 'missing_document'
                shipment.deficiency_notes = 'Demo scenario: one required pre-clearance document is missing.'
                shipment.deficiency_flagged_at = submitted_at + timedelta(days=1)
                shipment.save(update_fields=[
                    'has_deficiency', 'deficiency_type', 'deficiency_notes',
                    'deficiency_flagged_at',
                ])
            for doc_type in doc_types:
                ShipmentDocument.objects.create(
                    shipment=shipment,
                    document_type=doc_type,
                    file=f'demo/{hawb}_{doc_type}.pdf',
                    ocr_quality=random.choice(['good', 'good', 'fair']),
                    ocr_text=f'{desc}\nHS CODE {random.choice(hs_list).code if hs_list else ""}',
                    ocr_ran_at=submitted_at + timedelta(hours=2),
                )

            # ── Backdated status-log timeline ─────────────────────────────────
            ts = submitted_at
            computed_ts = None
            prev = 'incoming'
            for step_i, st in enumerate(path):
                if step_i > 0:
                    ts = ts + timedelta(
                        days=random.randint(0, 3),
                        hours=random.randint(1, 20),
                        minutes=random.randint(0, 59),
                    )
                    if ts > now:
                        ts = now - timedelta(minutes=random.randint(1, 120))
                log = StatusLog.objects.create(
                    shipment=shipment, changed_by=declarant or consignee,
                    old_status=prev, new_status=st, notes=SEED_NOTE,
                )
                StatusLog.objects.filter(pk=log.pk).update(changed_at=ts)
                if st == 'computed':
                    computed_ts = ts
                prev = st

            last_ts = ts

            # ── Computation + advisory for computed-and-beyond ────────────────
            if reached_computed and hs_list and declarant:
                hs            = random.choice(hs_list)
                duty_rate     = hs.duty_rate
                other_charges = exw_usd * Decimal('0.03')
                dv_usd        = exw_usd + freight + insurance + other_charges
                dv_php        = dv_usd * exchange_rate
                cud           = dv_php * (duty_rate / Decimal('100'))
                taxable_value = round(dv_php, 2)
                customs       = round(cud, 2)
                vat_base      = taxable_value + customs
                vat           = round(vat_base * Decimal('0.12'), 2)
                bf            = make_brokerage_fee(taxable_value)
                ipf           = make_ipf(taxable_value)
                tlc           = round(taxable_value + customs + vat + bf + Decimal('130') + ipf, 2)

                items = [{
                    'no': 1, 'description': desc, 'quantity': str(qty),
                    'exw': float(exw_usd), 'item_freight': float(freight),
                    'item_insurance': float(insurance), 'other_charges': float(other_charges),
                    'dv_usd': float(dv_usd), 'dv_php': float(dv_php), 'cud': float(cud),
                }]

                comp = DutyComputation.objects.create(
                    shipment=shipment, hs_code=hs, total_freight=freight,
                    total_insurance=insurance, exchange_rate=exchange_rate,
                    duty_rate=duty_rate, declared_value=exw_usd,
                    items_json=json.dumps(items), dutiable_value=taxable_value,
                    customs_duty=customs, vat_base=vat_base, vat_amount=vat,
                    brokerage_fee=bf, ipf=ipf, total_landed_cost=tlc,
                    container_type=stype, computed_by=declarant,
                )
                if computed_ts:
                    DutyComputation.objects.filter(pk=comp.pk).update(
                        computed_at=computed_ts, updated_at=computed_ts)

                ShipmentLineItem.objects.create(
                    shipment=shipment,
                    description=desc,
                    quantity=qty,
                    unit='PCS',
                    unit_price=round(exw_usd / qty, 4) if qty else exw_usd,
                    total_val_usd=exw_usd,
                    hs_code=hs,
                    is_confirmed=random.random() > 0.18,
                    source=random.choice(['ocr', 'manual']),
                    confidence=Decimal(str(round(random.uniform(0.55, 0.96), 4))),
                    row_order=1,
                    duty_rate=duty_rate,
                    gross_weight=weight,
                )
                ShipmentHSCode.objects.get_or_create(
                    shipment=shipment,
                    hs_code=hs,
                    defaults={'is_suggested': True, 'is_confirmed': True},
                )

                # Real MCDA scoring so recommended_type is authentic and may
                # differ from the consignee's chosen mode.
                if compute_wmcda:
                    try:
                        scores, recommended, _bd, _ex = compute_wmcda(
                            float(weight), float(volume), float(exw_usd), urgency, distance)
                        lcl_s = Decimal(str(scores['lcl']))
                        fcl_s = Decimal(str(scores['fcl']))
                        air_s = Decimal(str(scores['air']))
                    except Exception:
                        recommended = stype
                        lcl_s = fcl_s = air_s = Decimal(str(round(random.uniform(0.4, 0.8), 4)))
                else:
                    recommended = random.choices(SHIP_TYPES, weights=SHIP_WEIGHTS)[0]
                    lcl_s = Decimal(str(round(random.uniform(0.4, 0.8), 4)))
                    fcl_s = Decimal(str(round(random.uniform(0.4, 0.8), 4)))
                    air_s = Decimal(str(round(random.uniform(0.4, 0.8), 4)))

                ShippingAdvisory.objects.create(
                    shipment=shipment, gross_weight=weight, cargo_volume=volume,
                    declared_value=exw_usd, urgency_level=urgency,
                    distance_km=Decimal(str(distance)),
                    lcl_score=lcl_s, fcl_score=fcl_s, air_score=air_s,
                    recommended_type=recommended, computed_by=declarant,
                )

                Notification.objects.create(
                    recipient=consignee, shipment=shipment,
                    notification_type='computation',
                    title=f'Computation Ready — {hawb}',
                    message=f'Estimated Total Landed Cost: ₱{tlc:,.2f}',
                )

            # ── Status notification for terminal outcomes ─────────────────────
            if final in ('approved', 'rejected', 'billed'):
                Notification.objects.create(
                    recipient=consignee, shipment=shipment, notification_type=final,
                    title=f'Shipment {final.title()} — {hawb}',
                    message=f'Your shipment {hawb} is now marked {final}.',
                )

            # ── Feedback for billed shipments ─────────────────────────────────
            if final == 'billed':
                rating = random.choices([5, 4, 3, 2, 1], weights=[45, 30, 15, 6, 4])[0]
                fb = Feedback.objects.create(
                    consignee=consignee, shipment=shipment, rating=rating,
                    comment=random.choice(FEEDBACK_COMMENTS),
                    is_approved=random.random() < 0.65,
                )
                Feedback.objects.filter(pk=fb.pk).update(
                    created_at=last_ts + timedelta(days=random.randint(0, 3)))

            # ── Backdate shipment timestamps ──────────────────────────────────
            Shipment.objects.filter(pk=shipment.pk).update(
                submitted_at=submitted_at,
                updated_at=last_ts,
                processed_at=computed_ts,
            )

            status_tally[final] += 1
            created += 1

        # ── Summary ───────────────────────────────────────────────────────────
        self.stdout.write(self.style.SUCCESS(f'Seeded {created} shipments across ~{months} months.'))
        self.stdout.write('Status distribution:')
        for s in statuses:
            self.stdout.write(f'  {s:<13} {status_tally[s]}')
        self.stdout.write('-' * 50)
        self.stdout.write('Demo credentials (all passwords: Demo@1234)')
        self.stdout.write('  Consignees : consignee01 ... consignee10')
        self.stdout.write('  Declarants : declarant01, declarant02, declarant03')
        self.stdout.write('  Supervisor : supervisor01')
        self.stdout.write('-' * 50)
