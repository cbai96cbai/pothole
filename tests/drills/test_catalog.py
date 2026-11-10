import hashlib
from collections import Counter

from pothole.drills import catalog

FROZEN_SHA256 = "7c8082387ead08759cc6efe10c5dbdc8c4053d9aa1b9606f71f2c664e55a6493"


def test_catalog_is_frozen():
    digest = hashlib.sha256(catalog.CATALOG.read_bytes()).hexdigest()
    assert digest == FROZEN_SHA256


def test_catalog_is_what_the_rule_builds():
    assert catalog.load() == catalog.build()


def test_split_sizes():
    counts = Counter(c.split for c in catalog.load())
    assert counts == {"dev": 42, "test": 30}


def test_every_family_has_its_variants_in_the_split_the_table_gives():
    by = Counter((c.family, c.split) for c in catalog.load())
    for family, (_, dev, test) in catalog.FAMILIES.items():
        assert by[(family, "dev")] == len(dev)
        assert by[(family, "test")] == len(test)


def test_judged_days_are_three_other_recorded_days():
    for c in catalog.load():
        assert c.batch_seen in catalog.days()
        assert len(set(c.batch_judged)) == 3
        assert c.batch_seen not in c.batch_judged
        assert set(c.batch_judged) <= set(catalog.days())


def test_every_day_is_seen_in_a_six_variant_family():
    seen = {c.batch_seen for c in catalog.load() if c.family == "renamed_field"}
    assert seen == set(catalog.days())


def test_ids_and_seeds_are_unique():
    cs = catalog.load()
    assert len({c.id for c in cs}) == len(cs)
    assert len({c.seed for c in cs}) == len(cs)
