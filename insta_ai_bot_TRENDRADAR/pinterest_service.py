"""Scheduled Pinterest search; failure of one query does not stop the others."""
import os


def scan_pinterest_terms(terms, count_per_term=None):
    # Bounded independently of the Instagram setting to limit network requests.
    if os.getenv("PINTEREST_ENABLED", "1").lower() in ("0", "false", "no"):
        return [], ["Pinterest غیرفعال است."]
    try:
        import pinterest_extractor as extractor
    except ImportError as exc:
        return [], [f"Pinterest در دسترس نیست: {exc}"]
    try:
        limit = max(1, min(20, int(count_per_term or os.getenv("PINTEREST_RESULTS_PER_TERM", "10"))))
    except ValueError:
        limit = 10
    unique = {}
    errors = []
    for term in dict.fromkeys(terms):
        try:
            # Hashtag normalization uses underscores, Pinterest works better with words.
            pins = extractor.extract(term.replace("_", " "), count=limit, workers=3)
            for pin in pins:
                key = pin.get("pin_id") or pin.get("pin_url")
                if not key:
                    continue
                if key not in unique:
                    unique[key] = pin
                    pin["search_terms"] = []
                if term not in unique[key]["search_terms"]:
                    unique[key]["search_terms"].append(term)
        except Exception as exc:
            errors.append(f"{term}: {type(exc).__name__}: {exc}"[:240])
    try:
        return extractor.score_pins(list(unique.values())), errors
    except Exception as exc:
        return [], errors + [f"امتیازدهی Pinterest: {type(exc).__name__}: {exc}"[:240]]
