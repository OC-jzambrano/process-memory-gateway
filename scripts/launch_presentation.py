#!/usr/bin/env python3
"""
Launcher script for OPM (Odoo Process Memory) Executive Presentation Deck.
Opens the presentation in your default browser or displays instructions.
"""

import webbrowser
from pathlib import Path


def main() -> None:
    root_dir = Path(__file__).resolve().parent.parent
    pres_file = root_dir / "presentation.html"
    pdf_file = root_dir / "presentation" / "OPM_Executive_Presentation.pdf"

    print("=" * 70)
    print("  ODOO PROCESS MEMORY (OPM) — EXECUTIVE PRESENTATION DECK")
    print("=" * 70)
    print(f"Presentation File: {pres_file}")
    print(f"PDF Slide Deck:    {pdf_file}")
    print("-" * 70)
    print("Keyboard Shortcuts in Presentation:")
    print("  • [→] / [Space] / [PageDown] : Next Slide")
    print("  • [←] / [Backspace] / [PageUp]: Previous Slide")
    print("  • [F]                        : Toggle Fullscreen Mode")
    print("  • [N]                        : Toggle Speaker Notes Drawer")
    print("  • [T]                        : Toggle Dark / Light Theme")
    print("  • [P]                        : Print / Export 16:9 PDF")
    print("=" * 70)

    if pres_file.exists():
        uri = pres_file.as_uri()
        print(f"Opening presentation in default browser: {uri}")
        webbrowser.open(uri)
    else:
        print(f"Error: Presentation file not found at {pres_file}")


if __name__ == "__main__":
    main()
