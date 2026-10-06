import subprocess
from pathlib import Path

chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
base_dir = Path(__file__).resolve().parent

with open(base_dir / "index.html", "r", encoding="utf-8") as f:
    orig_html = f.read()

for slide_idx in [0, 1, 2]:
    for theme in ["dark", "white"]:
        code = orig_html
        if theme == "white":
            code = code.replace('data-theme="dark"', 'data-theme="light"')
        
        # Ensure static markup activates the right slide without relying on timing
        code = code.replace('class="slide active"', 'class="slide"')
        code = code.replace(f'id="slide-{slide_idx}"', f'id="slide-{slide_idx}" class="slide active"')
        
        # Override initial slide index in JS
        code = code.replace('let currentSlide = 0;', f'let currentSlide = {slide_idx};')

        tmp_file = base_dir / f"temp_{theme}_{slide_idx}.html"
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write(code)

        out_png = base_dir / f"fullscreen_slide_{slide_idx}_{theme}_1920x1080.png"
        cmd = [
            chrome_path,
            "--headless",
            "--disable-gpu",
            "--window-size=1920,1080",
            "--virtual-time-budget=2000",
            f"--screenshot={out_png}",
            tmp_file.as_uri()
        ]
        subprocess.run(cmd, check=True)
        tmp_file.unlink()
        print(f"Captured: {out_png.name}")

print("All screenshots successfully captured!")
