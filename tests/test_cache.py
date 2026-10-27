from pathlib import Path

from gp_agent.cache import DAY_S, GB, CacheEntry, plan_eviction

NOW = 1_800_000_000.0


def entry(name: str, size_gb: float, age_days: float) -> CacheEntry:
    return CacheEntry(path=Path(name), size_bytes=int(size_gb * GB), last_used=NOW - age_days * DAY_S)


class TestPlanEviction:
    def test_nothing_to_do_when_fresh_and_under_limit(self):
        assert plan_eviction([entry("a", 1, 1), entry("b", 2, 6)], now=NOW) == ()

    def test_evicts_entries_older_than_seven_days(self):
        result = plan_eviction([entry("old", 1, 8), entry("new", 1, 1)], now=NOW)
        assert result == (Path("old"),)

    def test_evicts_least_recently_used_until_under_limit(self):
        entries = [entry("a", 8, 1), entry("b", 8, 3), entry("c", 8, 2)]
        result = plan_eviction(entries, now=NOW, max_bytes=20 * GB)
        assert result == (Path("b"),)

    def test_expired_count_towards_freed_space(self):
        entries = [entry("old", 15, 9), entry("a", 8, 1), entry("b", 8, 2)]
        assert plan_eviction(entries, now=NOW, max_bytes=20 * GB) == (Path("old"),)

    def test_never_evicts_files_in_use(self):
        entries = [entry("busy", 15, 9), entry("a", 15, 1)]
        result = plan_eviction(entries, now=NOW, max_bytes=20 * GB, in_use=frozenset({Path("busy")}))
        assert result == (Path("a"),)

    def test_does_not_mutate_input(self):
        entries = [entry("a", 30, 1)]
        snapshot = list(entries)
        plan_eviction(entries, now=NOW)
        assert entries == snapshot
