"""Back-render PPTX pages, preferring PowerPoint on Windows and LibreOffice as fallback."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

import sys

PLUGIN_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PLUGIN_SCRIPTS))

from runtime_office import NoRenderBackendError, resolve_soffice


def render_powerpoint(input_path: Path, output: Path) -> int:
    import win32com.client  # type: ignore

    app = win32com.client.DispatchEx("PowerPoint.Application")
    presentation = None
    try:
        presentation = app.Presentations.Open(str(input_path), WithWindow=False)
        count = presentation.Slides.Count
        for index in range(1, count + 1):
            presentation.Slides(index).Export(str(output / f"slide_{index:03d}.png"), "PNG", 1792, 1008)
        return count
    finally:
        if presentation is not None:
            presentation.Close()
        app.Quit()


def render_libreoffice(input_path: Path, output: Path) -> int:
    soffice = resolve_soffice()
    if not soffice:
        raise NoRenderBackendError("LibreOffice executable was not found")
    import pypdfium2 as pdfium

    with tempfile.TemporaryDirectory(prefix="ppt-render-") as temp:
        profile = Path(temp) / "lo-profile"
        profile.mkdir()
        completed = subprocess.run(
            [soffice, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--convert-to", "pdf", "--outdir", temp, str(input_path)],
            capture_output=True, text=True, errors="replace", timeout=180, check=False,
        )
        pdf_path = Path(temp) / f"{input_path.stem}.pdf"
        if completed.returncode != 0 or not pdf_path.is_file():
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"LibreOffice conversion failed: {detail}")
        document = pdfium.PdfDocument(pdf_path)
        try:
            for index in range(len(document)):
                page = document[index]
                try:
                    page.render(scale=2).to_pil().convert("RGB").save(output / f"slide_{index + 1:03d}.png")
                finally:
                    page.close()
            return len(document)
        finally:
            document.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", choices=["auto", "powerpoint", "libreoffice"], default="auto")
    args = parser.parse_args()
    input_path = args.input.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    if args.backend in {"auto", "powerpoint"}:
        try:
            count = render_powerpoint(input_path, output)
            print(f"backend=powerpoint slides={count} output={output}")
            return 0
        except Exception as exc:
            errors.append(f"PowerPoint: {exc}")
            if args.backend == "powerpoint":
                raise
    if args.backend in {"auto", "libreoffice"}:
        try:
            count = render_libreoffice(input_path, output)
            print(f"backend=libreoffice slides={count} output={output}")
            return 0
        except Exception as exc:
            errors.append(f"LibreOffice: {exc}")
    raise SystemExit("No render backend succeeded. " + " | ".join(errors))


if __name__ == "__main__":
    raise SystemExit(main())
