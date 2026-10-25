from scripts.verify_temporal_migration import verify


def test_p02_history_source_partition_and_ids_survive_p03_upgrade(tmp_path):
    report = verify(tmp_path)
    assert report['old_ids_hashes_payloads_and_source_splits_preserved']
    assert report['repeated_upgrade_identical']
