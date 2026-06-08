from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('shipments', '0010_shipment_overdue_notified_at'),
        ('supervisor', '0004_increase_systemconfig_value_length'),
    ]

    operations = [
        migrations.CreateModel(
            name='IssueReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reporter_role', models.CharField(max_length=20)),
                ('category', models.CharField(choices=[('login_account', 'Login or Account'), ('shipment_submission', 'Shipment Submission'), ('document_upload', 'Document Upload'), ('ocr_extraction', 'OCR / Extraction'), ('duty_computation', 'Duty Computation'), ('wmcda_advisory', 'WMCDA Advisory'), ('notifications_email', 'Notifications or Email'), ('dashboard_analytics', 'Dashboard / Analytics'), ('page_display_ui', 'Page Display / UI'), ('other', 'Other')], max_length=40)),
                ('location', models.CharField(choices=[('dashboard', 'Dashboard'), ('new_submission', 'New Submission'), ('my_submissions', 'My Submissions'), ('process_shipment', 'Process Shipment'), ('ecdt_workspace', 'ECDT Workspace'), ('notifications', 'Notifications'), ('system_reference', 'System Reference'), ('other', 'Other')], max_length=40)),
                ('priority', models.CharField(choices=[('low', 'Low'), ('normal', 'Normal'), ('urgent', 'Urgent')], default='normal', max_length=20)),
                ('status', models.CharField(choices=[('open', 'Open'), ('in_review', 'In Review'), ('resolved', 'Resolved'), ('closed', 'Closed')], default='open', max_length=20)),
                ('title', models.CharField(max_length=160)),
                ('description', models.TextField()),
                ('attachment', models.FileField(blank=True, null=True, upload_to='issue_reports/')),
                ('supervisor_note', models.TextField(blank=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('handled_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='handled_issue_reports', to=settings.AUTH_USER_MODEL)),
                ('related_shipment', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='issue_reports', to='shipments.shipment')),
                ('reporter', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='issue_reports', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
