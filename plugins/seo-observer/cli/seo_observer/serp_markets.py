"""Resolve stored SERP populations to configured markets without merging contours."""


def language_key(value):
    return value.casefold().replace("_", "-").split("-")[0]


def competitor_scopes(config, market_id):
    market = next((m for m in config.markets if m.id == market_id), None)
    if market is None:
        if config.markets:
            raise ValueError(
                "SERP market must be resolved before selecting competitors"
            )
        return None  # Legacy project without market declarations.
    if "competitor_markets" in market.fields:
        return set(market.fields["competitor_markets"])
    language = language_key(market.language or market.locale)
    return {
        market.id,
        language,
        market.locale.casefold(),
        market.locale.casefold().replace("_", "-"),
    } | {
        m.id for m in config.markets if language_key(m.language or m.locale) == language
    }


def resolve_market(
    config, *, market_id=None, keyword_set_id=None, engine, region, source
):
    """Use explicit identity or keyword-set lineage; refuse ambiguous legacy data."""
    markets = {m.id: m for m in config.markets}
    if market_id in markets:
        return market_id
    if not markets:
        return market_id or "history-" + engine
    group = next((g for g in config.keyword_sets if g.id == keyword_set_id), None)
    candidates = [
        m
        for m in markets.values()
        if m.search_engine == engine and str(region) in m.regions
    ]
    if group:
        assigned = [m for m in candidates if m.id == group.market]
        if assigned:
            return assigned[0].id
        candidates = [
            m
            for m in candidates
            if language_key(m.language or m.locale) == language_key(group.locale)
        ]
    provider = [m for m in candidates if m.provider == source]
    if provider:
        candidates = provider
    if len(candidates) != 1:
        raise ValueError(
            "Historical SERP market is ambiguous; provide keyword-set market lineage"
        )
    return candidates[0].id
