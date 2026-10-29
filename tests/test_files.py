from pathlib import Path

import pytest

from gp_agent.files import MAX_TICKETS, TICKET_TTL_S, FileTickets, resolve_export_file


@pytest.fixture
def base(tmp_path):
    clip = tmp_path / "GamePulse" / "Braga vs Porto" / "Golo" / "Braga_Golo_01_12m30s.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"\x00" * 100)
    return tmp_path / "GamePulse"


REL = str(Path("Braga vs Porto") / "Golo" / "Braga_Golo_01_12m30s.mp4")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TestResolveExportFile:
    def test_relative_mp4_inside_base(self, base):
        assert resolve_export_file(base, REL) == (base / REL).resolve()

    def test_forward_slashes_accepted(self, base):
        assert resolve_export_file(base, "Braga vs Porto/Golo/Braga_Golo_01_12m30s.mp4") == (base / REL).resolve()

    @pytest.mark.parametrize(
        "rel",
        [
            "../secret.mp4",
            "Braga vs Porto/../../secret.mp4",
            "C:/Windows/x.mp4",
            "/etc/x.mp4",
            "\\\\server\\share\\x.mp4",
            "",
        ],
    )
    def test_rejects_paths_outside_base(self, base, rel):
        with pytest.raises(ValueError):
            resolve_export_file(base, rel)

    def test_rejects_other_extensions(self, base):
        (base / "notes.txt").write_text("x")
        with pytest.raises(ValueError):
            resolve_export_file(base, "notes.txt")

    def test_rejects_missing_file(self, base):
        with pytest.raises(FileNotFoundError):
            resolve_export_file(base, "Braga vs Porto/Golo/nao_existe.mp4")


class TestFileTickets:
    def test_issue_and_resolve(self, base):
        tickets = FileTickets(base)
        ticket = tickets.issue(REL)
        assert len(ticket) >= 32
        assert tickets.resolve(ticket) == (base / REL).resolve()

    def test_unknown_ticket(self, base):
        assert FileTickets(base).resolve("nope") is None

    def test_ticket_expires(self, base):
        clock = Clock()
        tickets = FileTickets(base, clock=clock)
        ticket = tickets.issue(REL)
        clock.now += TICKET_TTL_S + 1
        assert tickets.resolve(ticket) is None

    def test_invalid_path_not_issued(self, base):
        with pytest.raises(ValueError):
            FileTickets(base).issue("../x.mp4")

    def test_oldest_tickets_dropped_beyond_limit(self, base):
        tickets = FileTickets(base)
        first = tickets.issue(REL)
        for _ in range(MAX_TICKETS):
            tickets.issue(REL)
        assert tickets.resolve(first) is None
