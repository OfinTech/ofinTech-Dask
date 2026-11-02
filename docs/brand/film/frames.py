"""Paints the film one frame at a time and pipes JPEGs straight into ffmpeg: no PNG per frame left on disk.
   frames.py <start-sec> <end-sec> <out.mp4>   — a chunk, same settings as every other chunk.
   frames.py probe <t1,t2,...>                 — a few stills to look at before committing to a render."""
import functools, http.server, socketserver, subprocess, sys, threading, pathlib
from playwright.sync_api import sync_playwright

W, H, FPS = 1920, 1080, 30
HERE = pathlib.Path(__file__).parent
ROOT = HERE.parents[2]            # the repo root, so the page can reach site/assets
H_ = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
class Q(socketserver.TCPServer): allow_reuse_address = True
srv = Q(("127.0.0.1", 0), H_); PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

def open_page(p):
    b = p.chromium.launch(args=["--force-device-scale-factor=1", "--hide-scrollbars", "--disable-lcd-text"])
    pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
    pg.goto(f"http://127.0.0.1:{PORT}/docs/brand/film/film.html", wait_until="networkidle")
    pg.wait_for_function("window.__ready === true", timeout=30000)
    pg.wait_for_timeout(250)
    return b, pg

def main():
    with sync_playwright() as p:
        b, pg = open_page(p)
        if sys.argv[1] == "probe":
            for t in [float(x) for x in sys.argv[2].split(",")]:
                pg.evaluate("t => window.seek(t)", t)
                pg.wait_for_timeout(60)
                pg.screenshot(path=str(HERE / f"probe-{t:05.1f}.png"))
                print("probe", t)
            b.close(); srv.shutdown(); return
        a, z, out = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
        tmp = out + ".part.mp4"
        ff = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-vcodec", "mjpeg", "-framerate", str(FPS), "-i", "-",
             "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p",
             "-x264-params", "keyint=60:min-keyint=60:scenecut=0", "-r", str(FPS), tmp], stdin=subprocess.PIPE)
        n = int(round((z - a) * FPS))
        for i in range(n):
            pg.evaluate("t => window.seek(t)", a + i / FPS)
            ff.stdin.write(pg.screenshot(type="jpeg", quality=92))
            if i % 90 == 0: print(f"  {out}: {i}/{n}", flush=True)
        ff.stdin.close(); rc = ff.wait()
        b.close(); srv.shutdown()
        if rc != 0: sys.exit(f"ffmpeg exited {rc}")
        pathlib.Path(tmp).rename(out)          # only a complete render gets the real name
        print("wrote", out)

main()
