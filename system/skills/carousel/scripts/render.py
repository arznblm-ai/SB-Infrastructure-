#!/usr/bin/env python3
"""python3 render.py <папка_карусели> [--webkit]
<папка>/slides/NN.html -> <папка>/png/NN.png, 1080x1350. Путь абсолютный или относительный.
Движок: Playwright chromium (до 2 попыток, chromium из кэша, если ревизия разошлась), затем WKWebView.
После прогона висящие headless_shell убиваются."""
import sys, os, glob, time, subprocess
SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 1080, 1350
SLIDE_TIMEOUT = 20

def kill_chrome():
    subprocess.run(["pkill", "-f", "headless_shell"], capture_output=True)

def render_playwright(files, outdir):
    from playwright.sync_api import sync_playwright
    times = []
    with sync_playwright() as p:
        kw = {}
        if not os.path.exists(p.chromium.executable_path):  # ревизия кэша могла разойтись с версией пакета
            c = sorted(glob.glob(os.path.expanduser("~/Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-mac-arm64/chrome-headless-shell")))
            if c: kw["executable_path"] = c[-1]
        br = p.chromium.launch(headless=True, timeout=30000, **kw)
        try:
            ctx = br.new_context(viewport={"width": W, "height": H}, device_scale_factor=1)
            for f in files:
                t = time.time()
                pg = ctx.new_page()
                pg.set_default_timeout(SLIDE_TIMEOUT * 1000)
                pg.goto("file://" + os.path.abspath(f), wait_until="load", timeout=SLIDE_TIMEOUT * 1000)
                pg.evaluate("document.fonts.ready.then(()=>true)")
                pg.wait_for_timeout(400)
                out = os.path.join(outdir, os.path.splitext(os.path.basename(f))[0] + ".png")
                pg.screenshot(path=out, clip={"x": 0, "y": 0, "width": W, "height": H}, timeout=SLIDE_TIMEOUT * 1000)
                pg.close()
                times.append(time.time() - t)
                print(f"  {os.path.basename(out)} {times[-1]:.1f}s")
        finally:
            try: br.close()
            except Exception: pass
    return times

def read_root(f):
    """WKWebView читает файлы только внутри одного корня: общий предок слайда и design-system."""
    return os.path.commonpath([os.path.abspath(f), SKILL]) or "/"

def render_webkit(files, outdir):
    import objc
    from Foundation import NSURL, NSObject, NSRunLoop, NSDate
    from WebKit import WKWebView, WKWebViewConfiguration, WKSnapshotConfiguration
    from AppKit import NSApplication, NSMakeRect, NSBitmapImageRep, NSPNGFileType, NSWindow, NSBackingStoreBuffered
    NSApplication.sharedApplication()
    st = {}
    class D(NSObject):
        def webView_didFinishNavigation_(self, wv, nav): st["loaded"] = True
        def webView_didFailNavigation_withError_(self, wv, nav, e): st["fail"] = str(e)
        def webView_didFailProvisionalNavigation_withError_(self, wv, nav, e): st["fail"] = str(e)
    def pump(s): NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(s))
    times = []
    for f in files:
        t = time.time(); st.clear()
        cfg = WKWebViewConfiguration.alloc().init()
        view = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, W, H), cfg)
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(NSMakeRect(0, 0, W, H), 0, NSBackingStoreBuffered, False)
        win.setContentView_(view)
        d = D.alloc().init(); view.setNavigationDelegate_(d)
        url = NSURL.fileURLWithPath_(os.path.abspath(f))
        view.loadFileURL_allowingReadAccessToURL_(url, NSURL.fileURLWithPath_(read_root(f)))
        t0 = time.time()
        while not st and time.time() - t0 < SLIDE_TIMEOUT: pump(0.1)
        if "fail" in st or "loaded" not in st: raise RuntimeError("webkit load " + str(st))
        pump(2.5)
        sc = WKSnapshotConfiguration.alloc().init(); sc.setRect_(NSMakeRect(0, 0, W, H)); sc.setSnapshotWidth_(W)
        res = {}
        view.takeSnapshotWithConfiguration_completionHandler_(sc, lambda img, err: res.update(img=img, err=err))
        t0 = time.time()
        while "img" not in res and time.time() - t0 < SLIDE_TIMEOUT: pump(0.1)
        if not res.get("img"): raise RuntimeError("snapshot failed")
        rep = NSBitmapImageRep.imageRepWithData_(res["img"].TIFFRepresentation())
        data = rep.representationUsingType_properties_(NSPNGFileType, None)
        out = os.path.join(outdir, os.path.splitext(os.path.basename(f))[0] + ".png")
        data.writeToFile_atomically_(out, True)
        from PIL import Image  # WKWebView снимает в 2x на retina - приводим к 1080x1350
        im = Image.open(out)
        if im.size != (W, H): im.convert("RGB").resize((W, H), Image.LANCZOS).save(out)
        times.append(time.time() - t); print(f"  {os.path.basename(out)} {times[-1]:.1f}s (webkit)")
    return times

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args: sys.exit(__doc__)
    cdir = args[0]
    files = sorted(glob.glob(os.path.join(cdir, "slides", "[0-9][0-9].html")))
    if not files: sys.exit("нет slides/NN.html в " + cdir)
    outdir = os.path.join(cdir, "png"); os.makedirs(outdir, exist_ok=True)
    engine, times = None, None
    if "--webkit" not in sys.argv:
        for attempt in (1, 2):
            try:
                times = render_playwright(files, outdir); engine = "playwright-chromium"; break
            except Exception as e:
                print(f"playwright attempt {attempt} failed: {e}", file=sys.stderr)
            finally:
                kill_chrome()
    if times is None:
        times = render_webkit(files, outdir); engine = "wkwebview"
    left = subprocess.run(["pgrep", "-fl", "headless_shell"], capture_output=True, text=True).stdout.strip()
    if left: kill_chrome(); print("убиты висящие:", left)
    print(f"engine={engine} slides={len(files)} avg={sum(times)/len(times):.1f}s")

if __name__ == "__main__":
    main()
