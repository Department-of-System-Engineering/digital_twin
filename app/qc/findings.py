"""Stable, database-friendly observations; an observation is not always a defect."""

CATEGORIES = {
    "position": "placement", "relative_position": "placement",
    "angle_degrees": "orientation", "edge_relations": "alignment",
    "edge_geometry": "measurement", "nonconvex": "shape",
    "shape": "shape", "area_relative": "size", "part_count": "count",
    "missing": "count", "color": "color", "color_fraction": "color",
    "variant_mismatch": "variant", "unknown_or_ambiguous_variant": "measurement",
}


def findings(result):
    rows = []
    last = result.get("last_frame", {})
    parts = {p["name"]: p for p in last.get("parts", [])}
    for key, count in sorted(result.get("failure_counts", {}).items()):
        part, sep, code = key.partition(":")
        if not sep:
            code, part = part, None
        rows.append({"code": code, "category": CATEGORIES.get(code, "measurement"),
                     "part": part, "sample_count": count,
                     "metrics": parts.get(part, {}).get("metrics", {})})
    for relation in last.get("relations", []):
        if not relation["passed"]:
            rows.append({"code": "edge_relation", "category": "alignment",
                         "part": None, "sample_count": None, "metrics": relation})
    if result["status"] == "INCONCLUSIVE":
        rows.append({"code": result.get("reason", "insufficient_or_unstable_samples"),
                     "category": "measurement", "part": None,
                     "sample_count": None, "metrics": {}})
    return rows
