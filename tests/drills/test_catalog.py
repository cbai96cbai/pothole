import hashlib
import json
from collections import Counter

from pothole.drills import catalog, generate

FREEZE_SHA256 = "3b04d75cbb2b907a43469431c4bdd751e65ba26fb628bc0da86a62aa5cc29a96"


def test_catalog_is_frozen():
    """The freeze file is pinned here; it pins the catalog, the spec and the six batches."""
    assert hashlib.sha256(catalog.FREEZE.read_bytes()).hexdigest() == FREEZE_SHA256
    pinned = json.loads(catalog.FREEZE.read_text())
    assert generate.freeze_hashes() == pinned
    assert len(pinned) == 8  # catalog, spec, six batches


def test_catalog_is_what_the_generator_writes():
    assert json.loads(catalog.CATALOG.read_text()) == json.loads(json.dumps(generate.build()))


def test_split_sizes():
    counts = Counter(c.split for c in catalog.load())
    assert counts == {"dev": 42, "test": 30}


def test_every_family_has_its_variants_in_the_split_the_table_gives():
    by = Counter((c.family, c.split) for c in catalog.load())
    for family, (_, dev, test) in generate.FAMILIES.items():
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
