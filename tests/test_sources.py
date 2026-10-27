import pytest

from gp_agent.sources import SourceKind, classify_source, gdrive_file_id


class TestClassifySource:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/watch?v=dQw4w9WgXcQ&t=30",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/live/dQw4w9WgXcQ",
        ],
    )
    def test_youtube(self, url):
        assert classify_source(url) is SourceKind.YOUTUBE

    @pytest.mark.parametrize(
        "url",
        [
            "https://drive.google.com/file/d/1AbC_dEf-123/view?usp=sharing",
            "https://drive.google.com/open?id=1AbC_dEf-123",
            "https://drive.google.com/uc?id=1AbC_dEf-123&export=download",
        ],
    )
    def test_google_drive(self, url):
        assert classify_source(url) is SourceKind.GDRIVE

    def test_direct_mp4(self):
        assert classify_source("https://cdn.example.com/jogos/final.MP4?token=x") is SourceKind.DIRECT

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "not a url",
            "file:///C:/Windows/win.ini",
            "ftp://example.com/a.mp4",
            "http://www.youtube.com/watch?v=dQw4w9WgXcQ",  # https only
            "https://example.com/page.html",
            "https://youtube.com.evil.com/watch?v=x",
            "https://localhost/a.mp4",
            "https://127.0.0.1/a.mp4",
            "https://192.168.1.10/a.mp4",
            "https://[::1]/a.mp4",
            "https://user:pass@example.com/a.mp4",
        ],
    )
    def test_rejects_unsupported_or_unsafe(self, url):
        with pytest.raises(ValueError):
            classify_source(url)

    def test_rejects_non_string(self):
        with pytest.raises(ValueError):
            classify_source(None)


class TestGdriveFileId:
    @pytest.mark.parametrize(
        "url",
        [
            "https://drive.google.com/file/d/1AbC_dEf-123/view?usp=sharing",
            "https://drive.google.com/open?id=1AbC_dEf-123",
            "https://drive.google.com/uc?id=1AbC_dEf-123&export=download",
        ],
    )
    def test_extracts_id(self, url):
        assert gdrive_file_id(url) == "1AbC_dEf-123"

    def test_missing_id_raises(self):
        with pytest.raises(ValueError):
            gdrive_file_id("https://drive.google.com/drive/my-drive")
