import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from cra.case import CaseValidationError, parse_case
from investigations.models import CaseRecord


class Command(BaseCommand):
    help = "Validate and import case dataset JSON files into the Django database."

    def add_arguments(self, parser):
        parser.add_argument("--directory", type=Path, default=settings.BASE_DIR / "cases")

    def handle(self, *args, **options):
        paths = sorted(options["directory"].glob("*.json"))
        if not paths:
            raise CommandError(f"No case JSON files found in {options['directory']}")
        imported = 0
        for path in paths:
            try:
                dataset = json.loads(path.read_text(encoding="utf-8"))
                case = parse_case(dataset)
            except (OSError, json.JSONDecodeError, CaseValidationError) as exc:
                raise CommandError(f"Invalid case file {path}: {exc}") from exc
            for section in ("suspects", "witness_statements", "timeline"):
                dataset.setdefault(section, [])
            CaseRecord.objects.update_or_create(
                case_id=case.case_id,
                defaults={
                    "title": case.title,
                    "domain": case.domain,
                    "difficulty": case.difficulty,
                    "overview": case.overview,
                    "objective": case.objective,
                    "time_limit_minutes": case.time_limit_minutes,
                    "dataset": dataset,
                    "is_active": True,
                },
            )
            imported += 1
            self.stdout.write(f"Loaded {case.case_id}: {case.title}")
        self.stdout.write(self.style.SUCCESS(f"Imported {imported} case dataset(s)."))
