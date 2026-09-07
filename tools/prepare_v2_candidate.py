"""Inspect one explicit legacy snapshot and create a new, disabled candidate only."""

import argparse
import json
from pathlib import Path

from twitchbot.adapters.persistence import SQLiteDatabase
from twitchbot.application.persistence import PersistenceError
from twitchbot.migration import CandidateImporter, CandidateImportError, LegacySourceInspector
from twitchbot.migration.inspector import InspectionError


def prepare(source, downloads, candidate, reference):
    source, downloads, candidate = Path(source), Path(downloads), Path(candidate)
    if not candidate.is_absolute() or candidate.exists() or candidate.is_symlink() or candidate.parent.resolve() != candidate.parent or candidate.is_relative_to(source.resolve()) or candidate.is_relative_to(downloads.resolve()):
        raise CandidateImportError('candidate_target_exists_or_unsafe')
    database = SQLiteDatabase(candidate)
    inspector = LegacySourceInspector(source, downloads, reference)
    report = inspector.inspect()
    importer = CandidateImporter(source, downloads, reference, database)
    database.migrate()
    importer.import_report(report)
    verified = importer.verify_import(report)
    return verified.to_safe_mapping()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='Explicit canonical, sanitized legacy snapshot directory')
    parser.add_argument('--downloads', required=True, help='Explicit downloads root; video bodies are not copied')
    parser.add_argument('--candidate', required=True, help='New absolute .sqlite3 path outside both source roots')
    parser.add_argument('--reference', required=True, help='Non-secret operator label for this source snapshot')
    args = parser.parse_args(argv)
    try:
        result = prepare(args.source, args.downloads, args.candidate, args.reference)
    except (CandidateImportError, InspectionError, PersistenceError) as exc:
        print(json.dumps({'state': 'not_activated', 'error': exc.code}))
        return 1
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
