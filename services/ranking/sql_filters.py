from sqlalchemy import Select, select, text

from models.database.job import JobRecord
from models.database.profile import UserProfile


def build_candidate_query(
    profile: UserProfile,
    only_new: bool,
    *,
    skip_already_scored: bool = True,
) -> Select:
    """Return a SELECT statement (not executed here) for L1 candidate filtering.

    Only fields that are populated on the profile contribute clauses —
    missing/empty fields are silently skipped (best-effort, per design §10).
    """
    payload: dict = profile.payload

    stmt = (
        select(JobRecord)
        .where(
            JobRecord.embedding.is_not(None),
            JobRecord.embedded_at.is_not(None),
        )
        .order_by(JobRecord.scraped_at.desc())
        .limit(2000)
    )

    # --- Hard exclusions --------------------------------------------------

    roles_excluded: list[str] = payload.get("roles_excluded") or []
    if roles_excluded:
        patterns = [f"%{r.lower()}%" for r in roles_excluded]
        stmt = stmt.where(
            text("NOT (lower(jobs.title) LIKE ANY (:roles_excl_patterns))").bindparams(
                roles_excl_patterns=patterns
            )
        )

    industries_excluded: list[str] = payload.get("industries_excluded") or []
    if industries_excluded:
        stmt = stmt.where(
            text(
                "NOT EXISTS ("
                "  SELECT 1 FROM unnest(CAST(:industries_excl AS text[])) ix"
                "  WHERE lower(jobs.company) LIKE '%' || lower(ix) || '%'"
                "     OR lower(jobs.description) LIKE '%' || lower(ix) || '%'"
                ")"
            ).bindparams(industries_excl=industries_excluded)
        )

    deal_breakers: list[str] = payload.get("deal_breakers") or []
    if deal_breakers:
        stmt = stmt.where(
            text(
                "NOT EXISTS ("
                "  SELECT 1 FROM unnest(CAST(:deal_breakers AS text[])) kw"
                "  WHERE lower(jobs.description) LIKE '%' || lower(kw) || '%'"
                ")"
            ).bindparams(deal_breakers=deal_breakers)
        )

    # --- Work mode / location filters ------------------------------------

    work_mode: list[str] = payload.get("work_mode") or []
    location: dict = payload.get("location") or {}
    willing_to_relocate: bool = location.get("willing_to_relocate", True)
    city: str | None = location.get("city")

    if work_mode == ["remote"]:
        # Remote-only profile: only accept listings that signal remote.
        stmt = stmt.where(
            text(
                "lower(jobs.description) LIKE '%remote%'"
                " OR lower(jobs.title) LIKE '%remote%'"
                " OR lower(jobs.location) LIKE '%remote%'"
                " OR lower(jobs.description) LIKE '%remoto%'"
                " OR lower(jobs.description) LIKE '%teletrabajo%'"
            )
        )
    elif city and not willing_to_relocate and "remote" not in work_mode:
        # Onsite-only with no relocation: require city match or remote signal.
        stmt = stmt.where(
            text(
                "(lower(jobs.location) LIKE '%' || lower(:city) || '%'"
                " OR lower(jobs.description) LIKE '%remote%'"
                " OR lower(jobs.description) LIKE '%remoto%')"
            ).bindparams(city=city)
        )

    country = location.get("country")
    if country and not willing_to_relocate:
        # Block foreign-country jobs even when profile accepts remote — keep
        # only listings that mention the profile's country OR explicitly
        # target LATAM / América Latina.
        stmt = stmt.where(
            text(
                "(lower(jobs.location) LIKE '%' || lower(:country) || '%'"
                " OR lower(jobs.description) LIKE '%' || lower(:country) || '%'"
                " OR ((lower(jobs.location) LIKE '%remote%'"
                "      OR lower(jobs.location) LIKE '%remoto%')"
                "     AND (lower(jobs.description) LIKE '%latam%'"
                "       OR lower(jobs.description) LIKE '%latin america%'"
                "       OR lower(jobs.description) LIKE '%américa latina%'"
                "       OR lower(jobs.description) LIKE '%latinoamérica%')))"
            ).bindparams(country=country)
        )

    # --- only_new: exclude already-viewed jobs ---------------------------

    if only_new:
        stmt = stmt.where(
            text(
                "NOT EXISTS ("
                "  SELECT 1 FROM job_scores js"
                "  WHERE js.job_id = jobs.id"
                "    AND js.viewed_at IS NOT NULL"
                ")"
            )
        )

    if skip_already_scored:
        # Default: only score jobs that have no JobScore row yet.
        # Override with skip_already_scored=false to re-rank everything (e.g.
        # after a prompt / model / weights change).
        stmt = stmt.where(
            text(
                "NOT EXISTS ("
                "  SELECT 1 FROM job_scores js"
                "  WHERE js.job_id = jobs.id"
                ")"
            )
        )

    return stmt
