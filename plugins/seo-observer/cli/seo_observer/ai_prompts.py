from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable

from seo_observer.config import KeywordSet, ProjectConfig, observer_home
from seo_observer.storage import default_database_path

# Common region code mappings to natural language names for fluent long-tail prompt generation
KNOWN_REGION_NAMES: dict[str, str] = {
    "213": "Москва",
    "2": "Санкт-Петербург",
    "225": "Россия",
    "157": "Минск",
    "187": "Киев",
    "162": "Алматы",
    "2840": "United States",
    "840": "United States",
    "203": "United Kingdom",
    "236": "Germany",
    "250": "France",
}

DEFAULT_TEMPLATES: dict[str, dict[str, list[tuple[str, str]]]] = {
    "ru": {
        "discovery": [
            ("ru_disc_best", "Какой лучший выбор {keyword} и на что обратить внимание?"),
            ("ru_disc_recommend", "Посоветуй проверенные и надежные решения для {keyword}"),
            ("ru_disc_howto", "Как правильно выбрать {keyword}: подробная инструкция"),
            ("ru_disc_compare", "Сравнение и рейтинг вариантов: {keyword}"),
            ("ru_disc_pros_cons", "Плюсы и минусы различных решений для {keyword}"),
            ("ru_disc_overview", "Что нужно знать новичку перед тем, как выбрать {keyword}?"),
        ],
        "discovery_regional": [
            ("ru_disc_region_best", "Лучшие предложения и проверенные варианты: {keyword} в {region}"),
            ("ru_disc_region_find", "Где найти надежный сервис по {keyword} в {region}?"),
            ("ru_disc_region_reviews", "Рейтинг и отзывы: {keyword} в регионе {region}"),
        ],
        "branded_control_pair": [
            ("ru_brand_worth_it", "Стоит ли выбирать {brand} для {keyword}: реальные отзывы и опыт"),
            ("ru_brand_offer", "Что предлагает компания {brand} по направлению {keyword}?"),
            ("ru_brand_compare", "Чем {brand} отличается от конкурентов в сфере {keyword}?"),
            ("ru_brand_reviews", "Какие плюсы и минусы у {brand} при работе с {keyword}?"),
        ],
        "branded_control_direct": [
            ("ru_brand_direct_reviews", "Какие отзывы и рейтинг о {keyword}?"),
            ("ru_brand_direct_overview", "Что нужно знать перед выбором {keyword}?"),
            ("ru_brand_direct_alternatives", "Какие есть проверенные альтернативы и аналоги для {keyword}?"),
            ("ru_brand_direct_pros_cons", "Плюсы и минусы {keyword}: объективный обзор"),
        ],
    },
    "en": {
        "discovery": [
            ("en_disc_best", "What are the best options for {keyword} and what to look for?"),
            ("en_disc_recommend", "Can you recommend trusted and reliable solutions for {keyword}?"),
            ("en_disc_howto", "How to choose the right {keyword}: beginner's guide"),
            ("en_disc_compare", "Compare top providers and alternatives for {keyword}"),
            ("en_disc_pros_cons", "What are the pros and cons of different options for {keyword}?"),
            ("en_disc_overview", "What should I know before getting started with {keyword}?"),
        ],
        "discovery_regional": [
            ("en_disc_region_best", "Best options and top rated providers for {keyword} in {region}"),
            ("en_disc_region_find", "Where can I find reliable {keyword} in {region}?"),
            ("en_disc_region_reviews", "Reviews and ratings for {keyword} in {region}"),
        ],
        "branded_control_pair": [
            ("en_brand_worth_it", "Is {brand} a good choice for {keyword}? Real reviews and analysis"),
            ("en_brand_offer", "What does {brand} offer for {keyword}?"),
            ("en_brand_compare", "How does {brand} compare to competitors for {keyword}?"),
            ("en_brand_reviews", "What are the pros and cons of choosing {brand} for {keyword}?"),
        ],
        "branded_control_direct": [
            ("en_brand_direct_reviews", "What are user reviews and feedback about {keyword}?"),
            ("en_brand_direct_overview", "What should I know before choosing {keyword}?"),
            ("en_brand_direct_alternatives", "What are the top alternatives to {keyword}?"),
            ("en_brand_direct_pros_cons", "Pros and cons of {keyword}: detailed breakdown"),
        ],
    },
}


@dataclasses.dataclass(frozen=True)
class AIPrompt:
    prompt_id: str
    text: str
    cohort: str  # "discovery" or "branded_control"
    control: str  # "unbranded" or "branded"
    discovery_eligible: bool
    keyword: str
    template_id: str
    locale: str
    region: str | None = None
    paraphrased: bool = False
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_id": self.prompt_id,
            "text": self.text,
            "cohort": self.cohort,
            "control": self.control,
            "discovery_eligible": self.discovery_eligible,
            "keyword": self.keyword,
            "template_id": self.template_id,
            "locale": self.locale,
            "region": self.region,
            "paraphrased": self.paraphrased,
            "model": self.model,
        }

    def to_elmo_prompt(self) -> dict[str, Any]:
        return {
            "prompt_id": self.prompt_id,
            "text": self.text,
            "cohort": self.cohort,
            "control": self.control,
            "executed": False,
            "mentions": [],
            "citations": [],
        }


def compute_prompt_set_hash(prompt_rows: list[dict[str, Any]]) -> str:
    stable = [
        {
            "prompt_id": row["prompt_id"],
            "text": row["text"],
            "cohort": row["cohort"],
            "control": row["control"],
        }
        for row in sorted(prompt_rows, key=lambda item: item["prompt_id"])
    ]
    body = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def normalize_keywords(keywords: Iterable[str]) -> list[str]:
    groups: dict[str, list[str]] = {}
    for kw in keywords:
        if not kw:
            continue
        norm = " ".join(str(kw).strip().split())
        if not norm:
            continue
        key = norm.lower()
        if key not in groups:
            groups[key] = []
        if norm not in groups[key]:
            groups[key].append(norm)

    cleaned = []
    for key in sorted(groups.keys()):
        # Choose a deterministic representative independent of encounter order:
        # Prefer lowercase variant, then deterministic alphabetical tie-break
        candidates = sorted(groups[key], key=lambda s: (s != s.lower(), s))
        cleaned.append(candidates[0])
    return sorted(cleaned)


def normalize_brand_names(brand_names: Iterable[str]) -> list[str]:
    groups: dict[str, list[str]] = {}
    for b in brand_names:
        if not b:
            continue
        norm = " ".join(str(b).strip().split())
        if not norm:
            continue
        key = norm.lower()
        if key not in groups:
            groups[key] = []
        if norm not in groups[key]:
            groups[key].append(norm)

    cleaned = []
    for key in sorted(groups.keys()):
        # Choose a deterministic representative independent of encounter order:
        # Prefer lowercase variant, then deterministic alphabetical tie-break
        candidates = sorted(groups[key], key=lambda s: (s != s.lower(), s))
        cleaned.append(candidates[0])
    return sorted(cleaned)


def is_branded_text(text: str, brand_names: list[str]) -> bool:
    if not brand_names:
        return False
    for brand in brand_names:
        pattern = rf"(?<![\w\dа-яёА-ЯЁ]){re.escape(brand)}(?![\w\dа-яёА-ЯЁ])"
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _make_prompt_id(cohort: str, control: str, text: str) -> str:
    digest = hashlib.sha256(f"{cohort}:{control}:{text}".encode("utf-8")).hexdigest()[:12]
    prefix = "p-brand" if cohort == "branded_control" else "p-disc"
    return f"{prefix}-{digest}"


def resolve_region_name(region: str | None, config_location_name: str | None = None) -> str | None:
    if not region:
        return config_location_name
    region_str = str(region).strip()
    if region_str in KNOWN_REGION_NAMES:
        return KNOWN_REGION_NAMES[region_str]
    if region_str.isdigit() and config_location_name:
        return config_location_name
    return region_str


def generate_ai_prompts(
    keywords: Iterable[str],
    *,
    brand_names: Iterable[str] = (),
    locale: str = "ru-RU",
    region: str | None = None,
    include_regional: bool = True,
    include_branded_pairs: bool = True,
    custom_templates: dict[str, list[tuple[str, str]]] | None = None,
    llm_paraphrase: bool = False,
    llm_model: str | None = None,
    llm_fixture: Path | str | dict[str, str] | None = None,
    llm_adapter: Callable[[str], str] | None = None,
    limit: int | None = None,
) -> list[AIPrompt]:
    """Generates deterministic long-tail AI prompts from keywords.

    Non-branded discovery prompts and branded control prompts are cleanly separated.
    Branded prompts receive cohort="branded_control" and control="branded" (discovery_eligible=False)
    so they never enter discovery metrics.
    """
    sorted_keywords = normalize_keywords(keywords)
    normalized_brands = normalize_brand_names(brand_names)

    lang = "ru" if locale.lower().startswith("ru") else "en"
    lang_templates = DEFAULT_TEMPLATES.get(lang, DEFAULT_TEMPLATES["en"])

    disc_tpls = list(lang_templates["discovery"])
    reg_tpls = list(lang_templates["discovery_regional"]) if (region and include_regional) else []
    brand_pair_tpls = list(lang_templates["branded_control_pair"]) if (normalized_brands and include_branded_pairs) else []
    brand_direct_tpls = list(lang_templates["branded_control_direct"])

    if custom_templates:
        if "discovery" in custom_templates:
            disc_tpls.extend(custom_templates["discovery"])
        if "discovery_regional" in custom_templates and region and include_regional:
            reg_tpls.extend(custom_templates["discovery_regional"])
        if "branded_control_pair" in custom_templates and normalized_brands and include_branded_pairs:
            brand_pair_tpls.extend(custom_templates["branded_control_pair"])
        if "branded_control_direct" in custom_templates:
            brand_direct_tpls.extend(custom_templates["branded_control_direct"])

    seen_texts: set[str] = set()
    raw_prompts: list[dict[str, Any]] = []

    primary_brand = normalized_brands[0] if normalized_brands else ""

    for kw in sorted_keywords:
        kw_is_branded = is_branded_text(kw, normalized_brands)

        if kw_is_branded:
            # Keyword already contains brand: produce branded control prompts
            for tpl_id, tpl in brand_direct_tpls:
                text = tpl.format(keyword=kw).strip()
                if text.lower() not in seen_texts:
                    seen_texts.add(text.lower())
                    raw_prompts.append({
                        "text": text,
                        "cohort": "branded_control",
                        "control": "branded",
                        "discovery_eligible": False,
                        "keyword": kw,
                        "template_id": tpl_id,
                    })
        else:
            # Unbranded keyword: produce discovery prompts
            for tpl_id, tpl in disc_tpls:
                text = tpl.format(keyword=kw).strip()
                if text.lower() not in seen_texts:
                    seen_texts.add(text.lower())
                    raw_prompts.append({
                        "text": text,
                        "cohort": "discovery",
                        "control": "unbranded",
                        "discovery_eligible": True,
                        "keyword": kw,
                        "template_id": tpl_id,
                    })

            if reg_tpls and region:
                for tpl_id, tpl in reg_tpls:
                    text = tpl.format(keyword=kw, region=region).strip()
                    if text.lower() not in seen_texts:
                        seen_texts.add(text.lower())
                        raw_prompts.append({
                            "text": text,
                            "cohort": "discovery",
                            "control": "unbranded",
                            "discovery_eligible": True,
                            "keyword": kw,
                            "template_id": tpl_id,
                        })

            # Also generate branded control pairs for benchmarking
            if brand_pair_tpls and primary_brand:
                for tpl_id, tpl in brand_pair_tpls:
                    text = tpl.format(keyword=kw, brand=primary_brand).strip()
                    if text.lower() not in seen_texts:
                        seen_texts.add(text.lower())
                        raw_prompts.append({
                            "text": text,
                            "cohort": "branded_control",
                            "control": "branded",
                            "discovery_eligible": False,
                            "keyword": kw,
                            "template_id": tpl_id,
                        })

    # LLM Paraphrase (only under explicit flag)
    model_name = llm_model or "gpt-4o" if llm_paraphrase else None
    fixture_map: dict[str, str] = {}
    if llm_paraphrase and llm_fixture:
        if isinstance(llm_fixture, (str, Path)):
            fixture_path = Path(llm_fixture).expanduser()
            if fixture_path.exists():
                try:
                    loaded = json.loads(fixture_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        fixture_map = {str(k).strip(): str(v).strip() for k, v in loaded.items()}
                except (OSError, json.JSONDecodeError):
                    pass
        elif isinstance(llm_fixture, dict):
            fixture_map = {str(k).strip(): str(v).strip() for k, v in llm_fixture.items()}

    seen_post_texts: set[str] = set()
    seen_prompt_ids: set[str] = set()
    prompts: list[AIPrompt] = []
    for item in raw_prompts:
        orig_text = item["text"]
        text = orig_text
        paraphrased = False

        if llm_paraphrase:
            if text in fixture_map:
                text = fixture_map[text]
                paraphrased = True
            elif llm_adapter is not None:
                text = llm_adapter(text)
                paraphrased = True
            else:
                paraphrased = False

        # Ensure cohort integrity after paraphrase:
        # If text contains any brand, it MUST be branded_control, never discovery
        is_branded = is_branded_text(text, normalized_brands) or item["cohort"] == "branded_control"
        cohort = "branded_control" if is_branded else "discovery"
        control = "branded" if is_branded else "unbranded"
        discovery_eligible = (cohort != "branded_control" and control != "branded")

        prompt_id = _make_prompt_id(cohort, control, text)
        text_norm = " ".join(text.strip().split()).lower()
        if text_norm in seen_post_texts or prompt_id in seen_prompt_ids:
            continue
        seen_post_texts.add(text_norm)
        seen_prompt_ids.add(prompt_id)

        prompts.append(
            AIPrompt(
                prompt_id=prompt_id,
                text=text,
                cohort=cohort,
                control=control,
                discovery_eligible=discovery_eligible,
                keyword=item["keyword"],
                template_id=item["template_id"],
                locale=locale,
                region=region,
                paraphrased=paraphrased,
                model=model_name if paraphrased else None,
            )
        )

    # Sort deterministically by prompt_id
    prompts = sorted(prompts, key=lambda p: p.prompt_id)

    if limit is not None and limit > 0:
        prompts = prompts[:limit]

    return prompts


def generate_prompts_from_keyword_set(
    keyword_set: KeywordSet | Any,
    *,
    brand_names: Iterable[str] = (),
    config_location_name: str | None = None,
    region: str | None = None,
    llm_paraphrase: bool = False,
    llm_model: str | None = None,
    llm_fixture: Path | str | None = None,
    limit: int | None = None,
) -> list[AIPrompt]:
    keywords = load_keywords_from_file(keyword_set.path)
    locale = getattr(keyword_set, "locale", "ru-RU") or "ru-RU"
    effective_region = region
    if not effective_region and getattr(keyword_set, "regions", None):
        first_region = keyword_set.regions[0] if keyword_set.regions else None
        effective_region = resolve_region_name(first_region, config_location_name)
    else:
        effective_region = resolve_region_name(effective_region, config_location_name)

    return generate_ai_prompts(
        keywords=keywords,
        brand_names=brand_names,
        locale=locale,
        region=effective_region,
        llm_paraphrase=llm_paraphrase,
        llm_model=llm_model,
        llm_fixture=llm_fixture,
        limit=limit,
    )


def load_keywords_from_file(path: Path | str) -> list[str]:
    file_path = Path(path).expanduser()
    if not file_path.exists() or not file_path.is_file():
        return []
    lines = file_path.read_text(encoding="utf-8").splitlines()
    keywords = []
    for line in lines:
        cleaned = line.strip()
        if cleaned and not cleaned.startswith("#"):
            keywords.append(cleaned)
    return keywords


def load_keywords_from_gsc(
    project_namespace: str,
    home: Path | None = None,
) -> list[str]:
    db_path = default_database_path(project_namespace, home or observer_home())
    if not db_path.exists():
        return []
    import sqlite3
    try:
        with sqlite3.connect(db_path) as con:
            cursor = con.cursor()
            rows = cursor.execute(
                """
                SELECT DISTINCT query_text
                FROM search_performance
                WHERE project_id = ?
                  AND source = 'google_search_console'
                  AND is_current = 1
                  AND query_text != ''
                ORDER BY query_text
                """,
                (project_namespace,),
            ).fetchall()
            return normalize_keywords(row[0] for row in rows if row[0])
    except (sqlite3.DatabaseError, OSError):
        return []


def load_keywords_from_wordstat(
    project_namespace: str,
    home: Path | None = None,
) -> list[str]:
    db_path = default_database_path(project_namespace, home or observer_home())
    if not db_path.exists():
        return []
    import sqlite3
    keywords: set[str] = set()
    try:
        with sqlite3.connect(db_path) as con:
            cursor = con.cursor()
            # 1. Load from keyword_demand table
            try:
                demand_rows = cursor.execute(
                    """
                    SELECT DISTINCT keyword_id
                    FROM keyword_demand
                    WHERE project_id = ?
                      AND source IN ('wordstat', 'yandex_wordstat')
                      AND is_current = 1
                      AND keyword_id != ''
                    ORDER BY keyword_id
                    """,
                    (project_namespace,),
                ).fetchall()
                for (kw,) in demand_rows:
                    if kw:
                        keywords.add(str(kw))
            except sqlite3.OperationalError:
                pass

            # 2. Load from persisted raw artifacts via schema relations
            try:
                artifact_rows = cursor.execute(
                    """
                    SELECT ra.relative_path
                    FROM raw_artifacts ra
                    JOIN source_requests sr ON sr.request_id = ra.request_id
                    JOIN collection_runs cr ON cr.run_id = sr.run_id
                    WHERE cr.project_id = ?
                      AND sr.source IN ('wordstat', 'yandex_wordstat')
                    ORDER BY ra.created_at
                    """,
                    (project_namespace,),
                ).fetchall()
                raw_root = (home or observer_home()) / "projects" / project_namespace / "raw"
                for (rel_path,) in artifact_rows:
                    if not rel_path:
                        continue
                    artifact_file = raw_root / rel_path
                    if artifact_file.is_file():
                        try:
                            payload = json.loads(artifact_file.read_text(encoding="utf-8"))
                            if isinstance(payload, dict):
                                for obs in payload.get("observations", []):
                                    if isinstance(obs, dict):
                                        kw = obs.get("keyword") or obs.get("query")
                                        if kw:
                                            keywords.add(str(kw))
                                for row in payload.get("rows", []):
                                    if isinstance(row, dict):
                                        kw = row.get("keyword") or row.get("query")
                                        if kw:
                                            keywords.add(str(kw))
                        except (OSError, json.JSONDecodeError, TypeError):
                            pass
            except sqlite3.OperationalError:
                pass
        return normalize_keywords(keywords)
    except (sqlite3.DatabaseError, OSError):
        return []


def discover_brand_names(config: ProjectConfig, explicit_brands: Iterable[str] = ()) -> list[str]:
    brands = list(explicit_brands)
    if brands:
        return normalize_brand_names(brands)

    # Automatically derive brand names from project and properties
    namespace = config.project.namespace
    # Clean namespace (e.g. "demo-example" -> "demo", "samplebrand" -> "samplebrand")
    clean_ns = re.sub(r"[-_](?:example|core|prod|stage|test)$", "", namespace, flags=re.IGNORECASE)
    brands.append(clean_ns)

    # From competitors.owned_domains
    if config.competitors and config.competitors.owned_domains:
        for pattern in config.competitors.owned_domains:
            dom = pattern.split(":")[0]
            if dom.startswith("*."):
                dom = dom[2:]
            if dom.lower().startswith("www."):
                dom = dom[4:]
            clean = dom.split(".")[0]
            if clean and clean not in {"example", "test", "localhost"}:
                brands.append(clean)

    # From properties
    for prop in config.properties:
        try:
            from urllib.parse import urlparse
            netloc = urlparse(prop.url).netloc
            host = netloc.split(":")[0]
            if host.lower().startswith("www."):
                host = host[4:]
            clean_host = host.split(".")[0]
            if clean_host and clean_host not in {"example", "test", "localhost"}:
                brands.append(clean_host)
        except Exception:
            pass

    return normalize_brand_names(brands)


def build_ai_prompts_payload(
    config: ProjectConfig,
    *,
    keyword_set_id: str | None = None,
    keywords: list[str] | None = None,
    keyword_file: Path | str | None = None,
    brand_names: Iterable[str] = (),
    provider_brand_id: str | None = None,
    locale: str | None = None,
    region: str | None = None,
    from_gsc: bool = False,
    from_wordstat: bool = False,
    llm_paraphrase: bool = False,
    llm_model: str | None = None,
    llm_fixture: Path | str | None = None,
    limit: int | None = None,
    output_dir: Path | None = None,
    home: Path | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    effective_brands = discover_brand_names(config, brand_names)

    effective_provider_brand_id = provider_brand_id
    if effective_provider_brand_id is None and hasattr(config, "sources") and "elmo" in config.sources:
        effective_provider_brand_id = (
            config.sources["elmo"].fields.get("brand_id")
            or config.sources["elmo"].fields.get("elmo_brand_id")
        )
    if effective_provider_brand_id is None and hasattr(config, "providers") and "elmo" in config.providers:
        effective_provider_brand_id = getattr(config.providers["elmo"], "brand_id", None)
    if effective_provider_brand_id is None:
        effective_provider_brand_id = (
            getattr(config.project, "elmo_brand_id", None)
            or getattr(config.project, "provider_brand_id", None)
        )

    collected_keywords: list[str] = []
    if keywords:
        collected_keywords.extend(keywords)

    if keyword_file:
        collected_keywords.extend(load_keywords_from_file(keyword_file))

    selected_kw_set: KeywordSet | None = None
    if keyword_set_id:
        selected_kw_set = next((item for item in config.keyword_sets if item.id == keyword_set_id), None)
        if selected_kw_set is None:
            return {
                "ok": False,
                "error": {
                    "code": "KEYWORD_SET_NOT_FOUND",
                    "message": f"Keyword set was not found: {keyword_set_id}",
                    "details": {"keyword_set_id": keyword_set_id},
                },
            }
        collected_keywords.extend(load_keywords_from_file(selected_kw_set.path))
    elif not collected_keywords and not from_gsc and not from_wordstat:
        # Default to the first configured keyword set if available
        if config.keyword_sets:
            selected_kw_set = config.keyword_sets[0]
            collected_keywords.extend(load_keywords_from_file(selected_kw_set.path))

    if from_gsc:
        collected_keywords.extend(load_keywords_from_gsc(config.project.namespace, home))

    if from_wordstat:
        collected_keywords.extend(load_keywords_from_wordstat(config.project.namespace, home))

    if not collected_keywords:
        return {
            "ok": False,
            "error": {
                "code": "NO_KEYWORDS_FOUND",
                "message": "No keywords available to generate AI prompts.",
                "details": {
                    "keyword_set_id": keyword_set_id,
                    "from_gsc": from_gsc,
                    "from_wordstat": from_wordstat,
                },
            },
        }

    effective_locale = (
        locale
        or (selected_kw_set.locale if selected_kw_set else None)
        or config.default_language_code
        or "ru-RU"
    )

    kw_regions = selected_kw_set.regions if selected_kw_set and selected_kw_set.regions else ()
    region_arg = region or (kw_regions[0] if kw_regions else None) or config.default_location_code
    effective_region = resolve_region_name(str(region_arg) if region_arg else None, config.default_location_name)

    prompts = generate_ai_prompts(
        keywords=collected_keywords,
        brand_names=effective_brands,
        locale=effective_locale,
        region=effective_region,
        llm_paraphrase=llm_paraphrase,
        llm_model=llm_model,
        llm_fixture=llm_fixture,
        limit=limit,
    )

    prompt_dicts = [p.to_dict() for p in prompts]
    prompt_hash = compute_prompt_set_hash(prompt_dicts)

    now_iso = created_at or dt.datetime.now(dt.timezone.utc).isoformat()
    primary_property = config.properties[0].id if config.properties else "main"

    discovery_count = sum(1 for p in prompts if p.discovery_eligible)
    branded_count = sum(1 for p in prompts if not p.discovery_eligible)

    any_paraphrased = any(p.paraphrased for p in prompts)
    generator_meta = {
        "method": "llm_paraphrase" if any_paraphrased else "deterministic_template",
        "model": (llm_model or "gpt-4o") if any_paraphrased else None,
    }

    summary = {
        "total_prompts": len(prompts),
        "discovery_prompts": discovery_count,
        "branded_control_prompts": branded_count,
    }

    payload: dict[str, Any] = {
        "ok": True,
        "schema": "seo-observer.ai-prompts.v1",
        "project_id": config.project.namespace,
        "property_id": primary_property,
        "prompt_set_hash": prompt_hash,
        "created_at": now_iso,
        "locale": effective_locale,
        "region": effective_region,
        "keyword_set_id": selected_kw_set.id if selected_kw_set else keyword_set_id,
        "brand_names": effective_brands,
        "provider_brand_id": effective_provider_brand_id,
        "generator": generator_meta,
        "summary": summary,
        "prompts": prompt_dicts,
    }

    if output_dir:
        out_path = Path(output_dir).expanduser()
        written = write_ai_prompts_artifacts(payload, out_path)
        payload["artifacts"] = written

    return payload


def write_ai_prompts_artifacts(payload: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)

    extract_path = output_dir / "prompts-extract.json"
    csv_path = output_dir / "prompts-export.csv"
    query_fan_out_path = output_dir / "query-fan-out.json"
    report_path = output_dir / "prompts-report.md"
    manifest_path = output_dir / "manifest.json"

    # 1. prompts-extract.json
    extract_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2. prompts-export.csv (for manual provider upload to Elmo/GEORank)
    prompts = payload.get("prompts", [])
    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(["prompt_id", "text", "cohort", "control", "discovery_eligible", "keyword", "template_id", "locale", "region"])
    for p in prompts:
        writer.writerow([
            p.get("prompt_id", ""),
            p.get("text", ""),
            p.get("cohort", ""),
            p.get("control", ""),
            p.get("discovery_eligible", False),
            p.get("keyword", ""),
            p.get("template_id", ""),
            p.get("locale", ""),
            p.get("region", "") or "",
        ])
    csv_path.write_text(csv_buffer.getvalue(), encoding="utf-8")

    # 3. query-fan-out.json (Elmo endpoint payload format for direct import compatibility)
    fan_out_payload: dict[str, Any] = {
        "prompts": [
            {
                "prompt_id": p.get("prompt_id"),
                "text": p.get("text"),
                "cohort": p.get("cohort"),
                "control": p.get("control"),
                "executed": False,
                "mentions": [],
                "citations": [],
            }
            for p in prompts
        ],
    }
    p_brand_id = payload.get("provider_brand_id")
    if p_brand_id:
        fan_out_payload["brandId"] = p_brand_id
    query_fan_out_path.write_text(json.dumps(fan_out_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 4. prompts-report.md (Markdown summary)
    summary = payload.get("summary", {})
    gen = payload.get("generator", {})
    report_lines = [
        f"# AI Prompts Extract — {payload.get('project_id')}",
        "",
        f"- **Prompt Set Hash**: `{payload.get('prompt_set_hash')}`",
        f"- **Created At**: {payload.get('created_at')}",
        f"- **Locale**: `{payload.get('locale')}`",
        f"- **Region**: {payload.get('region') or 'None'}",
        f"- **Generator**: {gen.get('method')} (model: {gen.get('model') or 'None'})",
        "",
        "## Summary",
        "",
        f"- **Total Prompts**: {summary.get('total_prompts', 0)}",
        f"- **Discovery Prompts**: {summary.get('discovery_prompts', 0)}",
        f"- **Branded Control Prompts**: {summary.get('branded_control_prompts', 0)}",
        "",
        "## Sample Discovery Prompts",
        "",
    ]
    disc_sample = [p for p in prompts if p.get("discovery_eligible")][:5]
    for p in disc_sample:
        report_lines.append(f"- `{p['prompt_id']}`: {p['text']}")

    report_lines.extend(["", "## Sample Branded Control Prompts", ""])
    brand_sample = [p for p in prompts if not p.get("discovery_eligible")][:5]
    for p in brand_sample:
        report_lines.append(f"- `{p['prompt_id']}`: {p['text']}")

    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    # 5. manifest.json
    manifest = {
        "schema": "seo-observer.ai-prompts-manifest.v1",
        "project_id": payload.get("project_id"),
        "property_id": payload.get("property_id"),
        "prompt_set_hash": payload.get("prompt_set_hash"),
        "created_at": payload.get("created_at"),
        "generator": gen,
        "summary": summary,
        "files": {
            "extract": str(extract_path),
            "csv": str(csv_path),
            "query_fan_out": str(query_fan_out_path),
            "report": str(report_path),
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "extract_path": str(extract_path),
        "csv_path": str(csv_path),
        "query_fan_out_path": str(query_fan_out_path),
        "report_path": str(report_path),
        "manifest_path": str(manifest_path),
    }
