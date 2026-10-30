from pathlib import Path

import pytest

from gp_agent.naming import clip_filename, clip_path, compilation_path, format_timestamp, sanitize_segment


class TestSanitizeSegment:
    def test_keeps_plain_text(self):
        assert sanitize_segment("Golo Sofrido") == "Golo Sofrido"

    def test_removes_windows_invalid_chars(self):
        assert sanitize_segment('Estoril vs. Braga: 2/1 <final>?*|"') == "Estoril vs. Braga 21 final"

    def test_removes_control_chars_and_collapses_spaces(self):
        assert sanitize_segment("Trans.\t\n  Ofensiva") == "Trans. Ofensiva"

    def test_strips_trailing_dots_and_spaces(self):
        assert sanitize_segment("Jogo...  ") == "Jogo"

    def test_blocks_path_traversal(self):
        assert sanitize_segment("..") == "Sem_nome"
        assert ".." not in sanitize_segment("../../etc")

    def test_prefixes_reserved_windows_names(self):
        assert sanitize_segment("CON") == "_CON"
        assert sanitize_segment("com1") == "_com1"

    def test_empty_becomes_placeholder(self):
        assert sanitize_segment("   ") == "Sem_nome"
        assert sanitize_segment(None) == "Sem_nome"

    def test_truncates_long_text(self):
        assert len(sanitize_segment("a" * 200)) == 60

    def test_keeps_portuguese_accents(self):
        assert sanitize_segment("Transição Defensiva") == "Transição Defensiva"


class TestFormatTimestamp:
    @pytest.mark.parametrize(
        ("ms", "expected"),
        [(0, "00m00s"), (5_000, "00m05s"), (754_000, "12m34s"), (754_999, "12m34s"), (6_000_000, "100m00s")],
    )
    def test_formats_minutes_seconds(self, ms, expected):
        assert format_timestamp(ms) == expected

    def test_rejects_negative(self):
        with pytest.raises(ValueError):
            format_timestamp(-1)


class TestClipFilename:
    def test_builds_expected_pattern(self):
        assert clip_filename("Estoril vs Braga", "Golo", 3, 754_000) == "Estoril-vs-Braga_Golo_03_12m34s.mp4"

    def test_index_above_99_keeps_all_digits(self):
        assert clip_filename("J", "C", 120, 0) == "J_C_120_00m00s.mp4"

    def test_rejects_index_below_one(self):
        with pytest.raises(ValueError):
            clip_filename("J", "C", 0, 0)


class TestClipPath:
    def test_nests_game_and_category_folders(self, tmp_path: Path):
        path = clip_path(tmp_path, "Estoril vs Braga", "Golo Sofrido", 1, 65_000)
        assert path == tmp_path / "Estoril vs Braga" / "Golo Sofrido" / "Estoril-vs-Braga_Golo-Sofrido_01_01m05s.mp4"

    def test_hostile_names_stay_inside_base(self, tmp_path: Path):
        path = clip_path(tmp_path, "../../Windows", "..\\System32", 1, 0)
        assert path.resolve().is_relative_to(tmp_path.resolve())


class TestTeamFolder:
    def test_clip_goes_under_team_then_game(self, tmp_path: Path):
        path = clip_path(tmp_path, "vs Contacto (24-10)", "Canto", 2, 1_168_000, team="Lusa-Ad Braga")
        assert path == (
            tmp_path / "Lusa-Ad Braga" / "vs Contacto (24-10)" / "Canto" / "vs-Contacto-(24-10)_Canto_02_19m28s.mp4"
        )

    def test_hostile_team_stays_inside_base(self, tmp_path: Path):
        path = clip_path(tmp_path, "J", "C", 1, 0, team="../../..")
        assert path.resolve().is_relative_to(tmp_path.resolve())

    def test_compilation_without_team_keeps_game_folder(self, tmp_path: Path):
        assert compilation_path(tmp_path, "Jogo", "Golos") == tmp_path / "Jogo" / "Jogo_Compilado_Golos.mp4"

    def test_compilation_goes_under_team_then_game(self, tmp_path: Path):
        path = compilation_path(tmp_path, "vs Contacto", "Golos", team="Lusa")
        assert path == tmp_path / "Lusa" / "vs Contacto" / "vs-Contacto_Compilado_Golos.mp4"


class TestDossierFolder:
    def test_folder_replaces_game_and_category_folders(self, tmp_path: Path):
        path = clip_path(tmp_path, "2026-10-12 vs Porto", "Cantos", 2, 750_000, team="Braga", folder="Dossier Cantos")
        assert path == tmp_path / "Braga" / "Dossier Cantos" / "2026-10-12-vs-Porto_Cantos_02_12m30s.mp4"

    def test_folder_without_team_sits_at_the_top(self, tmp_path: Path):
        path = clip_path(tmp_path, "Jogo", "Cantos", 1, 0, folder="Dossier Cantos")
        assert path == tmp_path / "Dossier Cantos" / "Jogo_Cantos_01_00m00s.mp4"

    def test_blank_folder_keeps_the_usual_layout(self, tmp_path: Path):
        path = clip_path(tmp_path, "Jogo", "Cantos", 1, 0, team="Braga", folder="   ")
        assert path == tmp_path / "Braga" / "Jogo" / "Cantos" / "Jogo_Cantos_01_00m00s.mp4"

    def test_hostile_folder_stays_inside_base(self, tmp_path: Path):
        path = clip_path(tmp_path, "J", "C", 1, 0, team="Braga", folder="..\..\Windows")
        assert path.resolve().is_relative_to(tmp_path.resolve())
        assert path.parent.name == "Windows"
        assert path.parent.parent.name == "Braga"
