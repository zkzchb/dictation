"""Extract inline scripts from HTML and ask Node to parse them."""
import pathlib
import re
import subprocess
import tempfile


root = pathlib.Path(__file__).resolve().parents[1]
pages = sorted((root / "shared" / "web").glob("*.html"))
for page in pages:
    html = page.read_text(encoding="utf-8")
    scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, re.S | re.I)
    inline = [script for script in scripts if script.strip()]
    for index, script in enumerate(inline):
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8") as stream:
            stream.write(script)
            stream.flush()
            subprocess.run(
                ["node", "--check", stream.name], check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
    print(f"OK {page.relative_to(root)} ({len(inline)} inline script(s))")
