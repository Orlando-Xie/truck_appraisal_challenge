"""Canonicalisation of makes, model families and specs.

Listing titles are written by sellers, so the raw text is noisy:
``"Actros 1845 / EURO 5 / GIGA SPACE / NOWY TACHOGRAF / 2013 REJ"``. Comparable
matching is only as good as this normalisation, so it is done explicitly with a
brand-scoped family table rather than by string similarity.

The same functions run at scrape time and at inference time, so the spec the
vision stage produces lands in exactly the same feature space as the corpus.
"""

from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Makes
# ---------------------------------------------------------------------------

MAKE_ALIASES = {
    "mercedes": "Mercedes-Benz",
    "mercedes benz": "Mercedes-Benz",
    "mercedesbenz": "Mercedes-Benz",
    "mercedes-benz": "Mercedes-Benz",
    "mb": "Mercedes-Benz",
    "man": "MAN",
    "daf": "DAF",
    "volvo": "Volvo",
    "scania": "Scania",
    "renault": "Renault",
    "renault trucks": "Renault",
    "iveco": "IVECO",
    "ford": "Ford",
    "ford trucks": "Ford",
    "fordtrucks": "Ford",
    "bmc": "BMC",
    "kenworth": "Kenworth",
    "peterbilt": "Peterbilt",
    "freightliner": "Freightliner",
    "international": "International",
    "navistar": "International",
    "mack": "Mack",
    "western star": "Western Star",
    "westernstar": "Western Star",
    "sterling": "Sterling",
    "hino": "Hino",
    "isuzu": "Isuzu",
    "mitsubishi": "Mitsubishi Fuso",
    "fuso": "Mitsubishi Fuso",
    "sinotruk": "Sinotruk",
    "sinotruk howo": "Sinotruk",
    "howo": "Sinotruk",
    "shacman": "Shacman",
    "shaanxi": "Shacman",
    "faw": "FAW",
    "sitrak": "Sitrak",
    "dongfeng": "Dongfeng",
    "kamaz": "KAMAZ",
    "maz": "MAZ",
    "gaz": "GAZ",
    "ural": "Ural",
    "tatra": "Tatra",
    "daewoo": "Daewoo",
    "otokar": "Otokar",
    "temsa": "Temsa",
    "foden": "Foden",
    "erf": "ERF",
    "nissan": "Nissan",
    "toyota": "Toyota",
    "askam": "Askam",
}

# ---------------------------------------------------------------------------
# Model families, per make. Order matters: longer / more specific patterns are
# tried first so "FH16" wins over "FH" and "XG+" over "XG".
# ---------------------------------------------------------------------------

MODEL_FAMILIES: dict[str, list[tuple[str, str]]] = {
    "Mercedes-Benz": [
        (r"\bactros\b", "Actros"),
        (r"\barocs\b", "Arocs"),
        (r"\bantos\b", "Antos"),
        (r"\baxor\b", "Axor"),
        (r"\batego\b", "Atego"),
        (r"\beconic\b", "Econic"),
        (r"\bzetros\b", "Zetros"),
        (r"\bunimog\b", "Unimog"),
        (r"\bsprinter\b", "Sprinter"),
        (r"\bvario\b", "Vario"),
        (r"\bsk\b|\bnp\b", "SK"),
    ],
    "MAN": [
        (r"\btgx\b", "TGX"),
        (r"\btgs\b", "TGS"),
        (r"\btga\b", "TGA"),
        (r"\btgm\b", "TGM"),
        (r"\btgl\b", "TGL"),
        (r"\btge\b", "TGE"),
        (r"\bf\s?2000\b", "F2000"),
        (r"\bl\s?2000\b", "L2000"),
        (r"\bm\s?2000\b", "M2000"),
    ],
    "Scania": [
        # Scania's letter denotes the cab range; the digits are power.
        (r"\bs\s?[45678]\d{2}\b|\bs-?series\b", "S-series"),
        (r"\br\s?[3456789]\d{2}\b|\br-?series\b|\bv8\b", "R-series"),
        (r"\bg\s?[3456]\d{2}\b|\bg-?series\b", "G-series"),
        (r"\bp\s?[23456]\d{2}\b|\bp-?series\b", "P-series"),
        (r"\bl\s?[23456]\d{2}\b|\bl-?series\b", "L-series"),
        (r"\b1\d{2}[lm]?\b|\b[34]-?series\b", "4-series"),
        (r"\bt\s?\d{3}\b|\bt-?series\b", "T-series"),
    ],
    "DAF": [
        (r"\bxg\+|\bxg\s?plus", "XG+"),
        (r"\bxg\b", "XG"),
        (r"\bxd\b", "XD"),
        (r"\bxf\b|\b105\b|\b106\b|\b95xf\b", "XF"),
        (r"\bcf\b|\b85cf\b|\b75cf\b|\b65cf\b", "CF"),
        (r"\blf\b", "LF"),
    ],
    "Volvo": [
        (r"\bfh\s?16\b|\bfh16\b", "FH16"),
        (r"\bfh\b|\bfh\s?1[23]\b", "FH"),
        (r"\bfmx\b", "FMX"),
        (r"\bfm\b", "FM"),
        (r"\bfl\b", "FL"),
        (r"\bfe\b", "FE"),
        (r"\bvnl\b", "VNL"),
        (r"\bvnr\b", "VNR"),
        (r"\bvnm\b", "VNM"),
        (r"\bvah\b", "VAH"),
        (r"\bnh\b|\bf1[026]\b", "F-series"),
    ],
    "Renault": [
        (r"\bmagnum\b", "Magnum"),
        (r"\bpremium\b", "Premium"),
        (r"\bmidlum\b", "Midlum"),
        (r"\bkerax\b", "Kerax"),
        (r"\bt\s?-?high\b", "T-High"),
        (r"\bt\s?\d{3}\b|\bt-?series\b|^t$", "T"),
        (r"\bc\s?\d{3}\b|^c$", "C"),
        (r"\bk\s?\d{3}\b|^k$", "K"),
        (r"\bd\s?\d{2,3}\b|^d$", "D"),
        (r"\bmascott\b", "Mascott"),
        (r"\bmaster\b", "Master"),
    ],
    "IVECO": [
        (r"\bs-?way\b|\bsway\b", "S-Way"),
        (r"\bstralis\b|\bas440\b|\bat440\b", "Stralis"),
        (r"\btrakker\b", "Trakker"),
        (r"\bx-?way\b", "X-Way"),
        (r"\beurocargo\b", "Eurocargo"),
        (r"\beurotech\b", "EuroTech"),
        (r"\beurostar\b", "EuroStar"),
        (r"\beurotrakker\b", "EuroTrakker"),
        (r"\bdaily\b", "Daily"),
        (r"\bturbostar\b", "TurboStar"),
    ],
    "Ford": [
        (r"\bf-?max\b|\bfmax\b", "F-MAX"),
        (r"\bcargo\b", "Cargo"),
        (r"\btransit\b", "Transit"),
        (r"\bf-?\d{3}\b", "F-Series"),
    ],
    "BMC": [
        (r"\btugra\b|\btu[gğ]ra\b", "Tugra"),
        (r"\bpro\b", "Pro"),
        (r"\bfatih\b", "Fatih"),
        (r"\bprofesyonel\b", "Profesyonel"),
    ],
    "Kenworth": [
        (r"\bt-?680\b", "T680"),
        (r"\bt-?880\b", "T880"),
        (r"\bt-?800\b", "T800"),
        (r"\bt-?370\b", "T370"),
        (r"\bw-?900\b", "W900"),
    ],
    "Peterbilt": [
        (r"\b579\b", "579"),
        (r"\b389\b", "389"),
        (r"\b379\b", "379"),
        (r"\b567\b", "567"),
        (r"\b365\b", "365"),
        (r"\b384\b", "384"),
        (r"\b377\b", "377"),
    ],
    "Freightliner": [
        (r"\bcascadia\b", "Cascadia"),
        (r"\bcolumbia\b", "Columbia"),
        (r"\bcentury\b", "Century"),
        (r"\bcoronado\b", "Coronado"),
        (r"\bm2\b", "M2"),
        (r"\b114sd\b|\b122sd\b", "SD"),
    ],
    "International": [
        (r"\blonestar\b|\blone\s?star\b", "LoneStar"),
        (r"\bprostar\b", "ProStar"),
        (r"\blt\s?\d{3}\b|\blt625\b|\blt\b", "LT"),
        (r"\bhx\b", "HX"),
        (r"\b4300\b|\b4400\b", "4000-series"),
        (r"\b9900\b|\b9400\b", "9000-series"),
    ],
    "Mack": [
        (r"\banthem\b", "Anthem"),
        (r"\bpinnacle\b", "Pinnacle"),
        (r"\bgranite\b", "Granite"),
        (r"\bchu\b|\bch\d{3}\b|\bch\b", "CH"),
        (r"\bcxu\b|\bcx\b", "CX"),
        (r"\bcv\d{3}\b", "CV"),
        (r"\ban64\b|\bgr64\b", "Granite"),
    ],
    "Sinotruk": [(r"\bhowo\b", "HOWO"), (r"\bhohan\b", "HOHAN"), (r"\bsitrak\b", "Sitrak")],
    "Shacman": [(r"\bx3000\b", "X3000"), (r"\bx5000\b", "X5000"), (r"\bf3000\b", "F3000"), (r"\bf2000\b", "F2000")],
}

GENERIC_FAMILY_NOISE = {
    "truck",
    "tractor",
    "unit",
    "used",
    "new",
    "euro",
    "hp",
    "km",
    "lhd",
    "rhd",
    "auto",
    "automatic",
    "manual",
    "retarder",
    "adr",
    "ac",
    "4x2",
    "6x2",
    "6x4",
    "8x4",
}


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def canonical_make(raw: str | None) -> str:
    if not raw:
        return ""
    key = strip_accents(raw).strip().lower()
    key = re.sub(r"\s+", " ", key)
    if key in MAKE_ALIASES:
        return MAKE_ALIASES[key]
    # Try the leading token, so "Mercedes-Benz Actros" still resolves.
    for alias, canon in sorted(MAKE_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if key.startswith(alias):
            return canon
    return raw.strip().title()


def canonical_family(make: str, *texts: str | None) -> str:
    """Find the canonical model family for `make` inside any of `texts`."""
    make = canonical_make(make)
    blob = " ".join(strip_accents(t or "").lower() for t in texts)
    blob = re.sub(r"[_/|]+", " ", blob)
    for pattern, family in MODEL_FAMILIES.get(make, []):
        if re.search(pattern, blob):
            return family
    # No brand table hit: fall back to the first meaningful token.
    for token in re.findall(r"[a-z][a-z0-9\-]{1,}", blob):
        if token not in GENERIC_FAMILY_NOISE and len(token) >= 2:
            return token.upper()
    return ""


# Model designations that encode engine power.
_POWER_PATTERNS = [
    # Mercedes 1845 / 1848 / 2545 -> last two digits are power x10
    (r"\b(1[6-9]|2[0-6]|3[0-3])(\d{2})\b", lambda m: int(m.group(2)) * 10),
    # MAN 18.480 / 26.440
    (r"\b\d{2}\.(\d{3})\b", lambda m: int(m.group(1))),
    # Scania R450 / S500 / G410
    (r"\b[rsgpl]\s?-?([3-7]\d{2})\b", lambda m: int(m.group(1))),
    # DAF XF480 / XF 106.480
    (r"\bxf\s?-?(?:10[56]\.)?([3-6]\d{2})\b", lambda m: int(m.group(1))),
    # Volvo FH500 / FH16 750
    (r"\bfh\s?(?:16\s?)?-?([3-7]\d{2})\b", lambda m: int(m.group(1))),
    # Renault T480 / Magnum 480
    (r"\b[tck]\s?-?([3-5]\d{2})\b", lambda m: int(m.group(1))),
    # IVECO AS440S46 -> 460 hp
    (r"\bas\d{3}s(\d{2})\b", lambda m: int(m.group(1)) * 10),
]


def power_from_designation(text: str | None) -> int | None:
    """Recover engine power from the model designation.

    Roughly a quarter of listings omit the power field but nearly all include a
    model number that encodes it, and power is a real price driver.
    """
    if not text:
        return None
    blob = strip_accents(text).lower()
    for pattern, fn in _POWER_PATTERNS:
        m = re.search(pattern, blob)
        if m:
            try:
                hp = fn(m)
            except (ValueError, IndexError):
                continue
            if 150 <= hp <= 800:
                return hp
    return None


def normalize_axle(raw: str | None) -> str:
    if not raw:
        return ""
    m = re.search(r"\b(\d)\s*[x×]\s*(\d)\b", raw.lower())
    return f"{m.group(1)}x{m.group(2)}" if m else ""


def euro_number(raw: str | None) -> int | None:
    if not raw:
        return None
    m = re.search(r"euro\s*([0-9])", raw.lower())
    if m:
        return int(m.group(1))
    m = re.search(r"\b([1-7])\b", raw)
    return int(m.group(1)) if m else None


CONDITION_PENALTY_FLAGS = {
    "crashed": "crashed",
    "damaged": "crashed",
    "accident": "crashed",
    "with a defect": "defect",
    "defect": "defect",
    "for spare parts": "parts",
    "spare parts": "parts",
    "new": "new",
}


def normalize_condition(raw: str | None) -> str:
    if not raw:
        return "used"
    low = raw.lower()
    for needle, canon in CONDITION_PENALTY_FLAGS.items():
        if needle in low:
            return canon
    return "used"


BODY_TYPE_ALIASES = {
    "tractor_unit": "tractor_unit",
    "tractor unit": "tractor_unit",
    "truck tractor": "tractor_unit",
    "cekici": "tractor_unit",
    "çekici": "tractor_unit",
    "tipper": "tipper",
    "dump": "tipper",
    "damperli": "tipper",
    "box": "box",
    "kapali kasa": "box",
    "curtainside": "curtainside",
    "tent": "curtainside",
    "refrigerated": "refrigerated",
    "frigo": "refrigerated",
    "tanker": "tanker",
    "flatbed": "flatbed",
    "mixer": "mixer",
    "car_transporter": "car_transporter",
    "tow": "tow",
    "garbage": "garbage",
}


def normalize_body_type(raw: str | None) -> str:
    if not raw:
        return "other"
    low = strip_accents(raw).lower().strip()
    for needle, canon in BODY_TYPE_ALIASES.items():
        if needle in low:
            return canon
    return "other"



def canonical_generation(make: str | None, family: str | None, year, label: str = "") -> str:
    """Coarse generation bucket. MP4 vs MP5 is a price cliff that age alone smooths over."""
    blob = f"{label or ''} {family or ''} {make or ''}".lower()
    try:
        y = int(year) if year else 0
    except (TypeError, ValueError):
        y = 0
    if "mp5" in blob or "actros 5" in blob or "new generation actros" in blob:
        return "actros_mp5"
    if "mp4" in blob or "streamspace" in blob:
        return "actros_mp4"
    if "mp3" in blob:
        return "actros_mp3"
    if "next gen" in blob or "new gen" in blob or "s-series" in blob:
        return "scania_ng"
    if "streamline" in blob:
        return "scania_streamline"
    fam = (family or "").lower()
    if "actros" in fam and y:
        if y >= 2019:
            return "actros_mp5"
        if y >= 2011:
            return "actros_mp4"
        if y >= 2008:
            return "actros_mp3"
        return "actros_early"
    if ("tgx" in fam or "tgs" in fam) and y:
        return "man_tg_new" if y >= 2020 else "man_tg_euro6" if y >= 2013 else "man_tg_early"
    if fam in {"xf", "xf105", "xf106"} and y:
        return "daf_xf_new" if y >= 2021 else "daf_xf106" if y >= 2013 else "daf_xf105"
    if fam in {"fh", "fh16", "fh4", "fh5"} and y:
        return "volvo_fh5" if y >= 2021 else "volvo_fh4" if y >= 2012 else "volvo_fh3"
    if y >= 2019:
        return "era_2019plus"
    if y >= 2013:
        return "era_euro6"
    if y >= 2006:
        return "era_euro5"
    if y:
        return "era_pre_euro5"
    return "unknown"


def normalize_listing(row: dict) -> dict:
    """Add canonical columns to a scraped or inferred listing row."""
    make = canonical_make(row.get("make"))
    text_blob = " ".join(
        str(row.get(k) or "")
        for k in ("model_family", "model_variant", "title")
    )
    family = canonical_family(make, row.get("model_family"), row.get("model_variant"), row.get("title"))
    power = row.get("power_hp") or power_from_designation(text_blob)
    out = dict(row)
    out["make_canon"] = make
    out["family_canon"] = family
    out["power_hp_canon"] = power if power and 150 <= power <= 800 else None
    out["axle_canon"] = normalize_axle(row.get("axle_config"))
    out["euro_canon"] = euro_number(row.get("euro_class"))
    out["condition_canon"] = normalize_condition(row.get("condition_flag"))
    out["body_canon"] = normalize_body_type(row.get("body_type"))
    out["generation_canon"] = canonical_generation(
        make, family, row.get("year"), row.get("generation") or ""
    )
    return out
