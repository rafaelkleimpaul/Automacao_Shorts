"""
Hashtag booster — merges LLM-generated hashtags with curated high-performance
base tags per niche, then returns platform-optimized sets.

Platform limits:
  Instagram : up to 30 hashtags (mix of broad + niche + specific)
  YouTube   : up to 15 hashtags in description (Shorts best practice)
  TikTok    : up to 20 hashtags

To add a new niche, add a key to NICHE_BASE_HASHTAGS below.
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# Curated base hashtags per niche
# Ordered: broad → niche-specific → engagement-drivers
# ─────────────────────────────────────────────────────────────────────────────

NICHE_BASE_HASHTAGS: dict[str, list[str]] = {
    "finance": [
        # Broad reach
        "#Finance", "#Money", "#PersonalFinance", "#FinanceTips",
        "#MoneyTips", "#FinancialFreedom", "#WealthBuilding",
        # Investing
        "#Investing", "#InvestingTips", "#StockMarket", "#ETF",
        "#PassiveIncome", "#WealthMindset",
        # Savings & budgeting
        "#Budgeting", "#SaveMoney", "#FinancialLiteracy",
        "#MoneyManagement", "#DebtFree",
        # Shorts-specific engagement
        "#Shorts", "#FinanceShorts", "#MoneyShorts",
        "#LearnOnTikTok", "#FinanceEducation",
    ],
    # ── Future niches ────────────────────────────────────────────────────────
    # "crypto": [
    #     "#Crypto", "#Bitcoin", "#Ethereum", "#CryptoInvesting",
    #     "#BlockChain", "#DeFi", "#CryptoNews", "#Altcoin",
    #     "#CryptoEducation", "#Shorts",
    # ],
    # "real_estate": [
    #     "#RealEstate", "#RealEstateInvesting", "#PropertyInvesting",
    #     "#PassiveIncome", "#RealEstateTips", "#Shorts",
    # ],
}

# Platform-specific limits
PLATFORM_LIMITS: dict[str, int] = {
    "instagram": 30,
    "youtube":   15,
    "tiktok":    20,
}

# Mandatory hashtags always appended at the end, per platform (after dedup + cap)
PLATFORM_MANDATORY: dict[str, list[str]] = {
    "instagram": ["#reels", "#fy"],
    "youtube":   ["#shorts", "#fy"],
    "tiktok":    ["#fyp", "#fy"],
}


def _load_trending(niche: str) -> list[str]:
    """Load top trending hashtags from the hashtag_manager cache (if available)."""
    try:
        from pipeline.hashtag_manager import load_cached_hashtags
        return load_cached_hashtags(niche)[:20]   # top 20 trending
    except Exception:
        return []


def boost(
    llm_hashtags: list[str],
    niche: str = "finance",
    platform: str | None = None,
) -> list[str]:
    """
    Merge LLM-generated hashtags with trending (cached) + curated base tags.

    Priority order: LLM-specific → trending (dynamic) → curated base → mandatory

    Args:
        llm_hashtags : hashtags from the LLM script (may or may not have #)
        niche        : content niche key (e.g. "finance")
        platform     : optional platform name to apply the limit

    Returns:
        Deduplicated, # prefixed list, capped to the platform limit.
    """
    base     = NICHE_BASE_HASHTAGS.get(niche, [])
    trending = _load_trending(niche)

    # Normalise: ensure every tag starts with #
    def _normalise(tag: str) -> str:
        tag = tag.strip()
        return tag if tag.startswith("#") else f"#{tag}"

    normalised_llm  = [_normalise(t) for t in llm_hashtags if t.strip()]
    normalised_base = [_normalise(t) for t in base]

    # Mandatory platform tags (always included, deduped alongside the rest)
    mandatory = [_normalise(t) for t in PLATFORM_MANDATORY.get(platform or "", [])]

    normalised_trending = [_normalise(t) for t in trending]

    # Merge: LLM-specific → trending → curated base → mandatory
    seen: set[str] = set()
    merged: list[str] = []
    for tag in normalised_llm + normalised_trending + normalised_base + mandatory:
        key = tag.lower()
        if key not in seen:
            seen.add(key)
            merged.append(tag)

    limit = PLATFORM_LIMITS.get(platform or "", 9999)
    capped = merged[:limit]

    # Guarantee mandatory tags survive the cap by replacing the last N slots
    if platform and mandatory:
        missing = [t for t in mandatory if t.lower() not in {x.lower() for x in capped}]
        for tag in missing:
            if capped:
                capped[-1] = tag  # replace last non-mandatory slot
            else:
                capped.append(tag)

    return capped


def boost_for_all_platforms(
    llm_hashtags: list[str],
    niche: str = "finance",
) -> dict[str, list[str]]:
    """
    Return a dict with platform-optimized hashtag lists for all platforms.

    Returns:
        {"instagram": [...], "youtube": [...], "tiktok": [...], "default": [...]}
    """
    return {
        platform: boost(llm_hashtags, niche=niche, platform=platform)
        for platform in PLATFORM_LIMITS
    } | {"default": boost(llm_hashtags, niche=niche)}
