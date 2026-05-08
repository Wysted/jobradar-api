from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from dependencies.openai_client import get_openai_client  # noqa: E402
from scripts.cv_generator.ingestion.pdf_extractor import extract_text  # noqa: E402
from scripts.cv_generator.ingestion.persist import persist_profile  # noqa: E402
from services.cv_generator.ingestor import ingest_cv_text  # noqa: E402


async def _run(
    pdf_path: Path,
    known_links: dict | None,
    persist: bool,
    profile_id: str | None,
    output_json: str | None,
) -> None:
    print(f"→ Extrayendo texto de {pdf_path.name}...")
    text = extract_text(pdf_path)
    print(f"✓ {len(text)} caracteres extraídos.")

    print("→ Llamando al LLM de ingesta...")
    client = get_openai_client()
    payload = await ingest_cv_text(client, text, known_links=known_links or None)
    print("✓ Payload extraído.")

    summary = {
        "name": (payload.get("personal_info") or {}).get("name"),
        "email": (payload.get("personal_info") or {}).get("email"),
        "headline": payload.get("headline"),
        "total_years": payload.get("total_years"),
        "n_areas": len(payload.get("areas", [])),
        "n_techs": len(payload.get("technologies", [])),
        "n_experiences": len(payload.get("experiences", [])),
        "n_education": len(payload.get("education", [])),
    }
    print("\n=== Resumen ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if output_json:
        Path(output_json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n✓ JSON completo guardado en: {output_json}")

    if persist:
        print("\n→ Persistiendo en DB...")
        pid = await persist_profile(payload, profile_id)
        action = "actualizado" if profile_id else "creado"
        print(f"✓ Perfil {action}: {pid}")
    else:
        print("\n(preview mode — no se guardó en DB, usá --persist)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingesta CV PDF → user_profiles.payload")
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--profile-id", default=None)
    parser.add_argument("--linkedin", default=None)
    parser.add_argument("--github", default=None)
    parser.add_argument("--portfolio", default=None)
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()

    pdf_path = Path(args.pdf).expanduser()
    if not pdf_path.exists():
        print(f"✗ No existe: {pdf_path}")
        sys.exit(1)

    known_links = {
        k: v for k, v in {
            "linkedin": args.linkedin,
            "github": args.github,
            "portfolio": args.portfolio,
        }.items() if v
    }

    try:
        asyncio.run(_run(
            pdf_path, known_links or None, args.persist, args.profile_id, args.output_json
        ))
    except Exception as exc:
        print(f"✗ {type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
