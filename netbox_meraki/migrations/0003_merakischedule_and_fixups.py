from datetime import timedelta

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


ACTIVE_STATUSES = {"pending", "scheduled", "running"}


def _normalize_network_ids(value):
    if isinstance(value, str):
        return [value] if value else []
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(item) for item in list(value) if str(item)]


def _extract_legacy_schedule_fields(job):
    data = job.data or {}
    meraki_data = data.get("meraki") or {}

    return {
        "name": meraki_data.get("schedule_name") or job.name,
        "sync_mode": meraki_data.get("sync_mode") or data.get("sync_mode") or "review",
        "organization_id": meraki_data.get("organization_id") or data.get("organization_id") or "",
        "network_ids": _normalize_network_ids(meraki_data.get("network_ids") or data.get("network_ids")),
    }


def migrate_existing_schedules(apps, schema_editor):
    Job = apps.get_model("core", "Job")
    MerakiSchedule = apps.get_model("netbox_meraki", "MerakiSchedule")
    ScheduledJobTracker = apps.get_model("netbox_meraki", "ScheduledJobTracker")

    tracked_job_ids = set(
        Job.objects.filter(
            pk__in=ScheduledJobTracker.objects.values_list("netbox_job_id", flat=True),
            status__in=ACTIVE_STATUSES,
        ).values_list("pk", flat=True)
    )

    for job in Job.objects.all().order_by("created"):
        data = job.data or {}
        meraki_data = data.get("meraki") or {}
        explicitly_owned = bool(meraki_data.get("scheduled"))
        tracked_active = job.pk in tracked_job_ids
        if not explicitly_owned and not tracked_active:
            continue
        extracted = _extract_legacy_schedule_fields(job)

        interval_minutes = job.interval or (job.data or {}).get("interval") or None
        run_at = job.scheduled or job.created
        is_active = job.status in ACTIVE_STATUSES
        current_job = job if is_active else None
        last_job = job if explicitly_owned and not is_active else None
        next_run_at = None
        if current_job is not None:
            next_run_at = run_at
        elif last_job is not None and interval_minutes:
            next_run_at = (last_job.scheduled or last_job.created or run_at) + timedelta(minutes=interval_minutes)

        schedule = MerakiSchedule.objects.create(
            name=extracted["name"],
            sync_mode=extracted["sync_mode"],
            organization_id=extracted["organization_id"],
            network_ids=extracted["network_ids"],
            run_at=run_at,
            interval_minutes=interval_minutes,
            enabled=True if is_active else bool(interval_minutes),
            next_run_at=next_run_at,
            created_by=job.user,
            current_job=current_job,
            last_job=last_job,
        )

        payload = dict(job.data or {})
        payload["meraki"] = {
            "sync_mode": schedule.sync_mode,
            "organization_id": schedule.organization_id,
            "network_ids": schedule.network_ids,
            "scheduled": True,
            "schedule_id": schedule.pk,
            "schedule_name": schedule.name,
        }
        job.data = payload
        job.interval = None
        job.save(update_fields=["data", "interval"])


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0001_squashed_0005"),
        ("netbox_meraki", "0002_security_compat_remediation"),
    ]

    operations = [
        migrations.CreateModel(
            name="MerakiSchedule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=200)),
                ("sync_mode", models.CharField(choices=[("auto", "Auto Sync"), ("review", "Sync with Review"), ("dry_run", "Dry Run Only")], default="review", max_length=20)),
                ("organization_id", models.CharField(blank=True, max_length=64)),
                ("network_ids", models.JSONField(blank=True, default=list)),
                ("run_at", models.DateTimeField()),
                ("interval_minutes", models.PositiveIntegerField(blank=True, null=True)),
                ("enabled", models.BooleanField(default=True)),
                ("next_run_at", models.DateTimeField(blank=True, null=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("current_job", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="core.job")),
                ("last_job", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="core.job")),
            ],
            options={
                "verbose_name": "Meraki Schedule",
                "verbose_name_plural": "Meraki Schedules",
                "ordering": ["name", "pk"],
            },
        ),
        migrations.RunPython(migrate_existing_schedules, migrations.RunPython.noop),
    ]
