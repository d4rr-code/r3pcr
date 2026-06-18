from datetime import date, datetime
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.shipments.models import Shipment


class ShipmentKpiEtaTests(TestCase):
    def setUp(self):
        self.consignee = User.objects.create_user(
            username='kpi_consignee',
            password='x',
            role='consignee',
            email='kpi_consignee@test.local',
        )

    def _shipment(self, **extra):
        defaults = {
            'hawb_number': 'R3PCR-KPI-001',
            'consignee': self.consignee,
            'shipment_type': 'air',
        }
        defaults.update(extra)
        return Shipment.objects.create(**defaults)

    def test_airfreight_eta_uses_estimated_arrival_date(self):
        shipment = self._shipment(
            shipment_type='air',
            estimated_arrival_date=date(2026, 6, 18),
        )

        self.assertEqual(shipment.kpi_target_days, (2, 3))
        self.assertEqual(shipment.kpi_target_label, '2-3 days')
        self.assertEqual(shipment.kpi_base_date, date(2026, 6, 18))
        self.assertEqual(shipment.kpi_base_label, 'Estimated arrival')
        self.assertEqual(shipment.kpi_eta_start, date(2026, 6, 20))
        self.assertEqual(shipment.kpi_eta_end, date(2026, 6, 21))

    def test_lcl_and_fcl_have_expected_targets(self):
        lcl = self._shipment(
            hawb_number='R3PCR-KPI-LCL',
            shipment_type='lcl',
            estimated_arrival_date=date(2026, 6, 18),
        )
        fcl = self._shipment(
            hawb_number='R3PCR-KPI-FCL',
            shipment_type='fcl',
            estimated_arrival_date=date(2026, 6, 18),
        )

        self.assertEqual(lcl.kpi_target_days, (4, 5))
        self.assertEqual(lcl.kpi_eta_end, date(2026, 6, 23))
        self.assertEqual(fcl.kpi_target_days, (3, 4))
        self.assertEqual(fcl.kpi_eta_end, date(2026, 6, 22))

    def test_eta_falls_back_to_submission_date_when_arrival_date_missing(self):
        submitted_at = timezone.make_aware(datetime(2026, 6, 10, 9, 0))
        shipment = self._shipment(
            shipment_type='fcl',
            estimated_arrival_date=None,
        )
        Shipment.objects.filter(pk=shipment.pk).update(submitted_at=submitted_at)
        shipment.refresh_from_db()

        self.assertEqual(shipment.kpi_base_date, date(2026, 6, 10))
        self.assertEqual(shipment.kpi_base_label, 'Submission date')
        self.assertEqual(shipment.kpi_eta_start, date(2026, 6, 13))
        self.assertEqual(shipment.kpi_eta_end, date(2026, 6, 14))

    def test_eta_is_empty_without_shipment_type(self):
        shipment = self._shipment(shipment_type=None)

        self.assertIsNone(shipment.kpi_target_days)
        self.assertEqual(shipment.kpi_target_label, '')
        self.assertIsNone(shipment.kpi_eta_start)
        self.assertIsNone(shipment.kpi_eta_end)

    def test_kpi_timing_marks_active_shipments_delayed_after_eta_end(self):
        shipment = self._shipment(
            shipment_type='air',
            status='assessed',
            estimated_arrival_date=date(2026, 6, 18),
        )

        with patch('apps.shipments.models.timezone.localdate', return_value=date(2026, 6, 22)):
            self.assertEqual(shipment.kpi_timing_status, 'delayed')
            self.assertEqual(shipment.kpi_timing_label, 'Delayed')
            self.assertEqual(shipment.kpi_timing_help, '1 day past KPI target')

    def test_kpi_timing_marks_eta_window_due_soon(self):
        shipment = self._shipment(
            shipment_type='air',
            status='ongoing',
            estimated_arrival_date=date(2026, 6, 18),
        )

        with patch('apps.shipments.models.timezone.localdate', return_value=date(2026, 6, 20)):
            self.assertEqual(shipment.kpi_timing_status, 'due_soon')
            self.assertEqual(shipment.kpi_timing_label, 'Due Soon')
            self.assertEqual(shipment.kpi_timing_help, '1 day left in KPI window')

    def test_kpi_timing_does_not_delay_released_or_billed_shipments(self):
        released = self._shipment(
            hawb_number='R3PCR-KPI-REL',
            shipment_type='air',
            status='released',
            estimated_arrival_date=date(2026, 6, 18),
        )
        billed = self._shipment(
            hawb_number='R3PCR-KPI-BIL',
            shipment_type='lcl',
            status='billed',
            estimated_arrival_date=date(2026, 6, 18),
        )

        with patch('apps.shipments.models.timezone.localdate', return_value=date(2026, 6, 30)):
            self.assertEqual(released.kpi_timing_status, 'complete')
            self.assertEqual(billed.kpi_timing_status, 'complete')
