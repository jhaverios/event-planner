#!/usr/bin/env python3
"""Drive the broker registration form in a real browser and screenshot each step.

Why a browser: the form trigger has ignoreBots on, which correctly rejects curl
and WhatsApp's link previewer. Only a real browser exercises the whole path.

  python3 scripts/test-broker-form.py BRK042 [outdir]
"""
import hashlib, hmac, os, pathlib, sys, re

BASE = os.environ.get("N8N_BASE", "http://127.0.0.1:5678")
ENV  = pathlib.Path(__file__).resolve().parent.parent / "deploy" / ".env"

def secret():
    for line in ENV.read_text().splitlines():
        if line.startswith("BROKER_LINK_SECRET="):
            return line.split("=", 1)[1].strip()
    sys.exit("BROKER_LINK_SECRET not found in deploy/.env")

def sign(code):
    return hmac.new(secret().encode(), code.encode(), hashlib.sha256).hexdigest()[:16]

def main():
    broker = (sys.argv[1] if len(sys.argv) > 1 else "BRK042").upper()
    out = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/form-shots")
    out.mkdir(parents=True, exist_ok=True)
    url = f"{BASE}/form/register?broker={broker}&sig={sign(broker)}"
    print("opening", url)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        # The bundled Chromium build may not match the Playwright version, so
        # point at the installed binary instead of downloading another one.
        exe = os.environ.get("CHROMIUM_PATH") or next(
            (p for p in [
                "/opt/pw-browsers/chromium-1234/chrome-linux64/chrome",
                "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
            ] if os.path.exists(p)), None)
        b = pw.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe \
            else pw.chromium.launch(args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 420, "height": 900},
                        user_agent="Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
                                   "(KHTML, like Gecko) Chrome/140.0 Mobile Safari/537.36")
        pg.goto(url, wait_until="networkidle")
        pg.screenshot(path=out / "1-open.png", full_page=True)
        print("  step 1:", pg.title())

        pg.click("button[type=submit], button:has-text('Start')")
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(1200)
        pg.screenshot(path=out / "2-details.png", full_page=True)
        body = pg.inner_text("body")
        print("  step 2 fields:", [f for f in ("Event", "Client name", "Client mobile", "Consent") if f in body])

        # Dropdown: n8n renders a custom control, so click it and pick the first option.
        try:
            pg.click("text=Event", timeout=3000)
            pg.wait_for_timeout(400)
            opt = pg.locator("li, [role=option]").first
            chosen = opt.inner_text().strip()
            opt.click()
            print("  chose event:", chosen)
        except Exception as e:
            print("  dropdown interaction failed:", str(e)[:120])

        for label, value in [("Client name", "Test Client"), ("Client mobile", "9876500011")]:
            try:
                pg.fill(f"input[name='{label}'], input[placeholder*='{label.split()[-1]}']", value)
            except Exception:
                pass
        try:
            pg.check("input[type=checkbox]")
        except Exception:
            pass
        pg.screenshot(path=out / "3-filled.png", full_page=True)

        pg.click("button[type=submit]")
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(2000)
        pg.screenshot(path=out / "4-confirmation.png", full_page=True)
        text = pg.inner_text("body")
        print("  final page:", " | ".join(l for l in text.splitlines() if l.strip())[:250])
        b.close()
    print("screenshots in", out)

if __name__ == "__main__":
    main()
