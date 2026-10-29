import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_overlay import make_png

from gp_agent.edit_cmd import OverlaySegment, Pause
from gp_agent.exporter import CompileRequest, EditRequest
from gp_agent.ffmpeg_cmd import Probe
from gp_agent.files import FileTickets
from gp_agent.jobs import JobQueue
from gp_agent.pairing import MAX_ATTEMPTS, Pairing, TokenStore
from gp_agent.runner import RunnerError
from gp_agent.server import MAX_BODY_BYTES, Settings, create_app

ORIGIN = "https://gamepulsecoach.com"
DEV_ORIGIN = "https://gamepulse-deploy-abc123-team.vercel.app"
YT = "https://www.youtube.com/watch?v=abc123"


class InlineExecutor:
    def submit(self, fn, *args):
        fn(*args)


class DeferredExecutor:
    def __init__(self):
        self.pending = []

    def submit(self, fn, *args):
        self.pending.append((fn, args))

    def run_all(self):
        for fn, args in self.pending:
            fn(*args)


class Harness:
    def __init__(self, tmp_path, allow_dev=False, executor=None):
        self.requests = []
        self.export_dir = tmp_path / "GamePulse"
        self.tokens = TokenStore(tmp_path / "token")
        self.pairing = Pairing()
        self.codes = []

        def work(req, ctx):
            self.requests.append(req)
            return self.export_dir / "Jogo" / "Golo" / "clip.mp4"

        self.probe_result: Probe | Exception = Probe(1920, 1080, True, 10_000, fps=25.0)

        def probe(_path):
            if isinstance(self.probe_result, Exception):
                raise self.probe_result
            return self.probe_result

        self.queue = JobQueue(work, executor=executor or InlineExecutor())
        app = create_app(
            Settings(allow_dev=allow_dev, export_dir=self.export_dir),
            queue=self.queue,
            pairing=self.pairing,
            tokens=self.tokens,
            show_code=self.codes.append,
            tickets=FileTickets(self.export_dir),
            probe=probe,
        )
        self.client = TestClient(app, base_url="http://localhost:7777")

    def pair(self) -> str:
        self.client.post("/pair/start", headers={"Origin": ORIGIN})
        resp = self.client.post("/pair", json={"code": self.codes[-1]}, headers={"Origin": ORIGIN})
        return resp.json()["token"]


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


def clip_body(**over):
    base = dict(
        videoUrl=YT, timestampMs=65_000, preMs=5000, postMs=3000,
        game="Braga vs Porto", category="Golo", index=1,
    )
    return {**base, **over}


class TestHealth:
    def test_ok_with_cors_for_allowed_origin(self, h):
        resp = h.client.get("/health", headers={"Origin": ORIGIN})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["paired"] is False
        assert resp.headers["access-control-allow-origin"] == ORIGIN
        assert "origin" in resp.headers["vary"].lower()

    def test_without_origin_still_answers(self, h):
        assert h.client.get("/health").status_code == 200

    def test_paired_flag(self, h):
        h.pair()
        assert h.client.get("/health", headers={"Origin": ORIGIN}).json()["paired"] is True

    def test_foreign_origin_forbidden(self, h):
        resp = h.client.get("/health", headers={"Origin": "https://evil.com"})
        assert resp.status_code == 403
        assert "access-control-allow-origin" not in resp.headers


class TestHostCheck:
    @pytest.mark.parametrize("host", ["evil.com", "evil.com:7777", "192.168.1.5:7777"])
    def test_dns_rebinding_blocked(self, h, host):
        resp = h.client.get("/health", headers={"Host": host})
        assert resp.status_code == 400

    @pytest.mark.parametrize("host", ["localhost:7777", "127.0.0.1:7777"])
    def test_loopback_hosts_allowed(self, h, host):
        assert h.client.get("/health", headers={"Host": host}).status_code == 200


class TestPreflight:
    def test_allowed_origin_gets_cors_and_pna(self, h):
        resp = h.client.options(
            "/clip",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type, x-gp-token",
                "Access-Control-Request-Private-Network": "true",
            },
        )
        assert resp.status_code == 204
        assert resp.headers["access-control-allow-origin"] == ORIGIN
        assert "X-GP-Token" in resp.headers["access-control-allow-headers"]
        assert resp.headers["access-control-allow-private-network"] == "true"

    def test_foreign_origin_preflight_forbidden(self, h):
        resp = h.client.options("/clip", headers={"Origin": "https://evil.com"})
        assert resp.status_code == 403

    def test_dev_origin_only_in_dev_mode(self, h, tmp_path):
        assert h.client.get("/health", headers={"Origin": DEV_ORIGIN}).status_code == 403
        dev = Harness(tmp_path / "dev", allow_dev=True)
        assert dev.client.get("/health", headers={"Origin": DEV_ORIGIN}).status_code == 200


class TestPairing:
    def test_flow_returns_persisted_token(self, h):
        token = h.pair()
        assert token == h.tokens.load()

    def test_requires_origin(self, h):
        assert h.client.post("/pair/start").status_code == 403
        assert h.client.post("/pair", json={"code": "123456"}).status_code == 403

    def test_wrong_code(self, h):
        h.client.post("/pair/start", headers={"Origin": ORIGIN})
        wrong = "000000" if h.codes[-1] != "000000" else "111111"
        resp = h.client.post("/pair", json={"code": wrong}, headers={"Origin": ORIGIN})
        assert resp.status_code == 403
        assert "token" not in resp.json()

    def test_lockout(self, h):
        h.client.post("/pair/start", headers={"Origin": ORIGIN})
        for _ in range(MAX_ATTEMPTS):
            h.client.post("/pair", json={"code": "abcdef"}, headers={"Origin": ORIGIN})
        resp = h.client.post("/pair", json={"code": h.codes[-1]}, headers={"Origin": ORIGIN})
        assert resp.status_code == 429

    def test_bad_body(self, h):
        resp = h.client.post("/pair", json={"nope": 1}, headers={"Origin": ORIGIN})
        assert resp.status_code == 422


class TestClip:
    def test_requires_token(self, h):
        h.pair()
        resp = h.client.post("/clip", json=clip_body(), headers={"Origin": ORIGIN})
        assert resp.status_code == 401
        assert "error" in resp.json()
        assert resp.headers["access-control-allow-origin"] == ORIGIN
        assert h.requests == []

    def test_wrong_token(self, h):
        h.pair()
        resp = h.client.post("/clip", json=clip_body(), headers={"Origin": ORIGIN, "X-GP-Token": "x" * 43})
        assert resp.status_code == 401

    def test_unpaired_agent_rejects_any_token(self, h):
        resp = h.client.post("/clip", json=clip_body(), headers={"Origin": ORIGIN, "X-GP-Token": ""})
        assert resp.status_code == 401

    def test_requires_origin_even_with_token(self, h):
        token = h.pair()
        assert h.client.post("/clip", json=clip_body(), headers={"X-GP-Token": token}).status_code == 403

    def test_accepts_and_reports_job(self, h):
        token = h.pair()
        headers = {"Origin": ORIGIN, "X-GP-Token": token}
        resp = h.client.post("/clip", json=clip_body(), headers=headers)
        assert resp.status_code == 202
        job_id = resp.json()["jobId"]
        req = h.requests[0]
        assert (req.video_url, req.timestamp_ms, req.pre_ms, req.post_ms) == (YT, 65_000, 5000, 3000)
        assert (req.game, req.category, req.index) == ("Braga vs Porto", "Golo", 1)

        job = h.client.get(f"/jobs/{job_id}", headers=headers)
        assert job.status_code == 200
        body = job.json()
        assert body["status"] == "done"
        assert body["file"] == str(Path("Jogo") / "Golo" / "clip.mp4")
        assert body["error"] is None

    def test_unsupported_url_rejected_up_front(self, h):
        token = h.pair()
        resp = h.client.post(
            "/clip",
            json=clip_body(videoUrl="https://vimeo.com/123"),
            headers={"Origin": ORIGIN, "X-GP-Token": token},
        )
        assert resp.status_code == 400
        assert h.requests == []

    @pytest.mark.parametrize(
        "over",
        [
            {"timestampMs": -1},
            {"preMs": 10**9},
            {"postMs": -5},
            {"index": 0},
            {"game": "x" * 1000},
            {"videoUrl": "https://youtube.com/" + "a" * 3000},
            {"timestampMs": "abc"},
        ],
    )
    def test_invalid_fields(self, h, over):
        token = h.pair()
        resp = h.client.post("/clip", json=clip_body(**over), headers={"Origin": ORIGIN, "X-GP-Token": token})
        assert resp.status_code == 422
        assert h.requests == []


def b64_png(w=16, h=9) -> str:
    return base64.b64encode(make_png(w, h)).decode()


class TestClipOverlays:
    def test_overlays_are_decoded(self, h):
        token = h.pair()
        body = clip_body(overlays=[{"timestampMs": 64_000, "png": b64_png(1280, 720)}])
        resp = h.client.post("/clip", json=body, headers={"Origin": ORIGIN, "X-GP-Token": token})
        assert resp.status_code == 202
        (ov,) = h.requests[0].overlays
        assert ov.timestamp_ms == 64_000
        assert ov.png == make_png(1280, 720)

    @pytest.mark.parametrize("png", ["%%%not-base64", base64.b64encode(b"GIF89a....").decode()])
    def test_bad_image_rejected(self, h, png):
        token = h.pair()
        body = clip_body(overlays=[{"timestampMs": 64_000, "png": png}])
        resp = h.client.post("/clip", json=body, headers={"Origin": ORIGIN, "X-GP-Token": token})
        assert resp.status_code == 400
        assert h.requests == []

    def test_too_many_overlays(self, h):
        token = h.pair()
        body = clip_body(overlays=[{"timestampMs": 1, "png": b64_png()}] * 6)
        resp = h.client.post("/clip", json=body, headers={"Origin": ORIGIN, "X-GP-Token": token})
        assert resp.status_code == 422


def compile_body(**over):
    part = {k: v for k, v in clip_body().items() if k not in ("game", "category", "index")}
    return {"game": "Braga vs Porto", "title": "Golo", "clips": [part, {**part, "timestampMs": 90_000}], **over}


class TestCompile:
    def test_accepts_and_builds_request(self, h):
        token = h.pair()
        resp = h.client.post("/compile", json=compile_body(), headers={"Origin": ORIGIN, "X-GP-Token": token})
        assert resp.status_code == 202
        assert "jobId" in resp.json()
        req = h.requests[0]
        assert isinstance(req, CompileRequest)
        assert (req.game, req.title) == ("Braga vs Porto", "Golo")
        assert [c.timestamp_ms for c in req.clips] == [65_000, 90_000]
        assert [c.index for c in req.clips] == [1, 2]

    def test_requires_token(self, h):
        h.pair()
        assert h.client.post("/compile", json=compile_body(), headers={"Origin": ORIGIN}).status_code == 401

    @pytest.mark.parametrize("over", [{"clips": []}, {"title": "x" * 1000}])
    def test_invalid(self, h, over):
        token = h.pair()
        resp = h.client.post("/compile", json=compile_body(**over), headers={"Origin": ORIGIN, "X-GP-Token": token})
        assert resp.status_code == 422

    def test_one_bad_url_rejects_all(self, h):
        token = h.pair()
        body = compile_body()
        body["clips"][1]["videoUrl"] = "https://vimeo.com/1"
        resp = h.client.post("/compile", json=body, headers={"Origin": ORIGIN, "X-GP-Token": token})
        assert resp.status_code == 400
        assert h.requests == []


def test_oversized_body_rejected(h):
    token = h.pair()
    headers = {"Origin": ORIGIN, "X-GP-Token": token, "Content-Type": "application/json",
               "Content-Length": str(MAX_BODY_BYTES + 1)}
    resp = h.client.post("/clip", content=b"{}", headers=headers)
    assert resp.status_code == 413


class TestJobs:
    def test_requires_token(self, h):
        h.pair()
        assert h.client.get("/jobs/abc", headers={"Origin": ORIGIN}).status_code == 401

    def test_unknown_job(self, h):
        token = h.pair()
        resp = h.client.get("/jobs/abc", headers={"Origin": ORIGIN, "X-GP-Token": token})
        assert resp.status_code == 404


CLIP_BYTES = bytes(range(100))
CLIP_REL = "Jogo/Golo/clip.mp4"


def write_clip(h):
    path = h.export_dir / "Jogo" / "Golo" / "clip.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(CLIP_BYTES)


def ticket_url(h, token):
    resp = h.client.post("/files/ticket", json={"path": CLIP_REL}, headers={"Origin": ORIGIN, "X-GP-Token": token})
    assert resp.status_code == 200
    return resp.json()["url"]


class TestFileTicket:
    def test_requires_token(self, h):
        write_clip(h)
        h.pair()
        resp = h.client.post("/files/ticket", json={"path": CLIP_REL}, headers={"Origin": ORIGIN})
        assert resp.status_code == 401

    def test_returns_url_and_video_info(self, h):
        write_clip(h)
        token = h.pair()
        resp = h.client.post("/files/ticket", json={"path": CLIP_REL}, headers={"Origin": ORIGIN, "X-GP-Token": token})
        body = resp.json()
        assert body["url"].startswith("/files/")
        assert (body["width"], body["height"], body["fps"], body["durationMs"]) == (1920, 1080, 25.0, 10_000)

    def test_unknown_fps_is_null(self, h):
        write_clip(h)
        h.probe_result = Probe(1280, 720, False, 5_000)
        token = h.pair()
        resp = h.client.post("/files/ticket", json={"path": CLIP_REL}, headers={"Origin": ORIGIN, "X-GP-Token": token})
        assert resp.json()["fps"] is None

    def test_path_outside_export_dir(self, h):
        token = h.pair()
        resp = h.client.post("/files/ticket", json={"path": "../x.mp4"}, headers={"Origin": ORIGIN, "X-GP-Token": token})
        assert resp.status_code == 400

    def test_missing_clip(self, h):
        token = h.pair()
        resp = h.client.post("/files/ticket", json={"path": CLIP_REL}, headers={"Origin": ORIGIN, "X-GP-Token": token})
        assert resp.status_code == 404
        assert "neste PC" in resp.json()["error"]

    def test_unreadable_clip(self, h):
        write_clip(h)
        h.probe_result = RunnerError("Leitura do vídeo falhou")
        token = h.pair()
        resp = h.client.post("/files/ticket", json={"path": CLIP_REL}, headers={"Origin": ORIGIN, "X-GP-Token": token})
        assert resp.status_code == 422


class TestServeFile:
    def test_full_file(self, h):
        write_clip(h)
        url = ticket_url(h, h.pair())
        resp = h.client.get(url, headers={"Origin": ORIGIN})
        assert resp.status_code == 200
        assert resp.content == CLIP_BYTES
        assert resp.headers["content-type"] == "video/mp4"
        assert resp.headers["accept-ranges"] == "bytes"
        assert resp.headers["access-control-allow-origin"] == ORIGIN

    def test_range_request(self, h):
        write_clip(h)
        url = ticket_url(h, h.pair())
        resp = h.client.get(url, headers={"Origin": ORIGIN, "Range": "bytes=10-19"})
        assert resp.status_code == 206
        assert resp.content == CLIP_BYTES[10:20]
        assert resp.headers["content-range"] == "bytes 10-19/100"

    def test_unknown_ticket(self, h):
        assert h.client.get("/files/nope", headers={"Origin": ORIGIN}).status_code == 404

    def test_requires_origin(self, h):
        write_clip(h)
        url = ticket_url(h, h.pair())
        assert h.client.get(url).status_code == 403


def edit_body(**over):
    png = base64.b64encode(make_png(64, 36)).decode()
    base = dict(
        path=CLIP_REL,
        pauses=[{"atMs": 2000, "durMs": 1500}],
        segments=[{"image": 0, "durMs": 2000}, {"image": 1, "durMs": 9500}],
        images=[png, png],
    )
    return {**base, **over}


class TestEdit:
    def test_requires_token(self, h):
        write_clip(h)
        h.pair()
        assert h.client.post("/edit", json=edit_body(), headers={"Origin": ORIGIN}).status_code == 401

    def test_accepts_and_builds_request(self, h):
        write_clip(h)
        token = h.pair()
        resp = h.client.post("/edit", json=edit_body(), headers={"Origin": ORIGIN, "X-GP-Token": token})
        assert resp.status_code == 202
        req = h.requests[-1]
        assert isinstance(req, EditRequest)
        assert req.src == (h.export_dir / "Jogo" / "Golo" / "clip.mp4").resolve()
        assert req.pauses == (Pause(2000, 1500),)
        assert req.segments == (OverlaySegment(0, 2000), OverlaySegment(1, 9500))
        assert req.images[0] == make_png(64, 36)

    def test_path_outside_export_dir(self, h):
        token = h.pair()
        resp = h.client.post("/edit", json=edit_body(path="../x.mp4"), headers={"Origin": ORIGIN, "X-GP-Token": token})
        assert resp.status_code == 400

    def test_missing_clip(self, h):
        token = h.pair()
        resp = h.client.post("/edit", json=edit_body(), headers={"Origin": ORIGIN, "X-GP-Token": token})
        assert resp.status_code == 404

    def test_segment_pointing_to_missing_image(self, h):
        write_clip(h)
        token = h.pair()
        body = edit_body(segments=[{"image": 5, "durMs": 1000}])
        resp = h.client.post("/edit", json=body, headers={"Origin": ORIGIN, "X-GP-Token": token})
        assert resp.status_code == 400
        assert h.requests == []

    def test_bad_image(self, h):
        write_clip(h)
        token = h.pair()
        resp = h.client.post("/edit", json=edit_body(images=["bm9wZQ=="]), headers={"Origin": ORIGIN, "X-GP-Token": token})
        assert resp.status_code == 400

    @pytest.mark.parametrize("over", [
        {"pauses": [{"atMs": 1000, "durMs": 100}]},
        {"pauses": [{"atMs": 1000, "durMs": 60_000}]},
        {"segments": []},
        {"segments": [{"image": 0, "durMs": 0}]},
        {"images": []},
    ])
    def test_invalid_fields(self, h, over):
        write_clip(h)
        token = h.pair()
        resp = h.client.post("/edit", json=edit_body(**over), headers={"Origin": ORIGIN, "X-GP-Token": token})
        assert resp.status_code == 422


class TestJobProgressAndCancel:
    def test_progress_is_reported(self, h):
        token = h.pair()
        job_id = h.client.post("/clip", json=clip_body(), headers={"Origin": ORIGIN, "X-GP-Token": token}).json()["jobId"]
        body = h.client.get(f"/jobs/{job_id}", headers={"Origin": ORIGIN, "X-GP-Token": token}).json()
        assert body["status"] == "done"
        assert body["progress"] is None

    def test_cancel_queued_job(self, tmp_path):
        ex = DeferredExecutor()
        h = Harness(tmp_path, executor=ex)
        token = h.pair()
        headers = {"Origin": ORIGIN, "X-GP-Token": token}
        job_id = h.client.post("/clip", json=clip_body(), headers=headers).json()["jobId"]
        assert h.client.post(f"/jobs/{job_id}/cancel", headers=headers).status_code == 200
        ex.run_all()
        assert h.client.get(f"/jobs/{job_id}", headers=headers).json()["status"] == "cancelled"
        assert h.requests == []

    def test_cancel_finished_job(self, h):
        token = h.pair()
        headers = {"Origin": ORIGIN, "X-GP-Token": token}
        job_id = h.client.post("/clip", json=clip_body(), headers=headers).json()["jobId"]
        assert h.client.post(f"/jobs/{job_id}/cancel", headers=headers).status_code == 409

    def test_cancel_unknown_and_needs_token(self, h):
        token = h.pair()
        assert h.client.post("/jobs/nope/cancel", headers={"Origin": ORIGIN, "X-GP-Token": token}).status_code == 404
        assert h.client.post("/jobs/nope/cancel", headers={"Origin": ORIGIN}).status_code == 401


def test_unknown_route_404(h):
    assert h.client.get("/nope", headers={"Origin": ORIGIN}).status_code == 404
