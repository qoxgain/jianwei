"""Explicit offline approval for generated OLD BUSINESS fixtures, never archives."""
from infra.sqlite_admission import digest_separate_process


def approve_generated_legacy(db):
    return db.register_legacy({'reviewer':'generated-fixture-operator','reference':'isolated-test-business-fixture',
        'expected_sha256':digest_separate_process(db.local_path),'writers_stopped':True,
        'consumption_reviewed':True,'legacy_business':True})
