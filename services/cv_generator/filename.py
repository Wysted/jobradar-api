import re
import unicodedata

_SLUG_RE = re.compile(r"[^A-Za-z0-9\-]+")
_MAX_PART = 80


def slugify(text: str) -> str:
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_RE.sub("_", ascii_text).strip("_")
    return slug[:_MAX_PART]


def build_filename(profile, job) -> str:
    rut = getattr(profile, "rut", None) or (profile.payload or {}).get("rut")
    if not rut:
        raise ValueError("profile.rut required for CV filename")

    personal_info = (profile.payload or {}).get("personal_info") or {}
    nombre = personal_info.get("name") or ""
    if not nombre:
        raise ValueError("profile.personal_info.name required for CV filename")

    nombre_slug = slugify(nombre)
    if not nombre_slug:
        raise ValueError("nombre cannot be empty after slugify")

    parts = [nombre_slug, rut]
    company_slug = slugify(job.company or "")
    if company_slug:
        parts.append(company_slug)
    cargo_slug = slugify(job.title or "")
    if cargo_slug:
        parts.append(cargo_slug)

    return "_".join(parts) + ".pdf"
