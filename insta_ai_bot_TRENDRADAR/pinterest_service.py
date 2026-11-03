"""Scheduled Pinterest text search with auditable AI query translation."""
import os
import re

import ai_service
from search_identity import plain_search_text

_PERSIAN = re.compile(r"[\u0600-\u06FF]")


def _english_query(value):
    return ai_service._valid_english_query(value)


def prepare_pinterest_queries(terms, *, topic=""):
    """Return unique English searches plus a source-by-source audit trail."""
    sources = []
    for term in terms or []:
        if not isinstance(term, str):
            continue
        source = term.strip()
        if not source or not ai_service.normalize_search_keyword(source):
            continue
        sources.append({"id": f"q{len(sources)}", "source": source,
                        "text": plain_search_text(source)})
    needs_ai = [q for q in sources if _PERSIAN.search(q["text"])]
    translations, translation_error = ai_service.translate_pinterest_queries(
        [{"id": q["id"], "source": q["text"] or q["source"]} for q in needs_ai], topic=topic)
    audit, unique, by_query = [], {}, {}
    for item in sources:
        if _PERSIAN.search(item["text"]):
            english = _english_query(translations.get(item["id"], ""))
            status, method = ("translated", "ai") if english else ("unavailable", "ai")
        else:
            english = _english_query(item["text"])
            status, method = ("unchanged", "passthrough") if english else ("unavailable", "passthrough")
        row = {"source_term": item["source"], "english_query": english,
               "status": status, "method": method}
        if not english and translation_error:
            row["error"] = translation_error
        audit.append(row)
        if not english:
            continue
        dedup_key = " ".join(english.casefold().split())
        if dedup_key not in by_query:
            entry = {"query": english, "sources": []}
            by_query[dedup_key] = entry
            unique[dedup_key] = entry
        if item["source"] not in by_query[dedup_key]["sources"]:
            by_query[dedup_key]["sources"].append(item["source"])
    return list(unique.values()), audit, translation_error


def scan_pinterest_terms(terms, count_per_term=None, *, topic="", return_queries=False):
    """Search each unique English query once; keep original-term provenance.

    Persian/mixed queries without a valid AI translation are skipped and reported,
    never searched as a fake English translation. Existing English queries are
    only whitespace/hashtag-normalized. One platform failure does not stop others.
    """
    disabled = os.getenv("PINTEREST_ENABLED", "1").lower() in ("0", "false", "no")
    if disabled:
        audit = {"queries": [], "search_queries": [], "term_results": {str(t): "skipped" for t in terms or [] if isinstance(t, str)}}
        return ([], ["Pinterest غیرفعال است."], audit) if return_queries else ([], ["Pinterest غیرفعال است."])
    queries, query_audit, translation_error = prepare_pinterest_queries(terms, topic=topic)
    term_results = {row["source_term"]: ("succeeded" if row["english_query"] else "failed")
                    for row in query_audit}
    audit = {"queries": query_audit, "search_queries": [q["query"] for q in queries],
             "term_results": term_results}
    errors = []
    if translation_error:
        errors.append(f"ترجمه Pinterest: {translation_error}")
    for row in query_audit:
        if not row["english_query"]:
            errors.append(f"{row['source_term']}: ترجمه انگلیسی معتبر در دسترس نیست؛ جست‌وجو انجام نشد.")
    if not queries:
        return ([], errors, audit) if return_queries else ([], errors)
    try:
        import pinterest_extractor as extractor
    except ImportError as exc:
        errors.append(f"Pinterest در دسترس نیست: {type(exc).__name__}")
        for key in term_results:
            if term_results[key] == "succeeded":
                term_results[key] = "failed"
        return ([], errors, audit) if return_queries else ([], errors)
    try:
        limit = max(1, min(20, int(count_per_term or os.getenv("PINTEREST_RESULTS_PER_TERM", "10"))))
    except ValueError:
        limit = 10
    unique = {}
    for entry in queries:
        query = entry["query"]
        try:
            pins = extractor.extract(query, count=limit, workers=3)
            for pin in pins:
                key = pin.get("pin_id") or pin.get("pin_url")
                if not key:
                    continue
                key = str(key)
                if key not in unique:
                    unique[key] = dict(pin)
                    unique[key]["search_terms"] = []
                    unique[key]["search_queries"] = []
                for source in entry["sources"]:
                    if source not in unique[key]["search_terms"]:
                        unique[key]["search_terms"].append(source)
                if query not in unique[key]["search_queries"]:
                    unique[key]["search_queries"].append(query)
        except Exception as exc:
            errors.append(f"{query}: {type(exc).__name__}")
            for source in entry["sources"]:
                term_results[source] = "failed"
    pins = list(unique.values())
    try:
        pins = extractor.score_pins(pins)
    except Exception as exc:
        errors.append(f"امتیازدهی Pinterest: {type(exc).__name__}؛ نتایج بدون امتیاز حفظ شدند.")
        for pin in pins:
            pin.setdefault("score", None)
    return (pins, errors, audit) if return_queries else (pins, errors)
