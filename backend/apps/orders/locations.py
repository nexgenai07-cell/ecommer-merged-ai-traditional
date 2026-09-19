# PATH: apps/orders/locations.py
#
# City -> Province data + matching helpers, used by checkout to make sure
# the city a customer types actually belongs to the province they picked
# in the dropdown (e.g. "Gujranwala" + "Sindh" is rejected — Gujranwala is
# in Punjab).
#
# HOW IT BEHAVES (on purpose):
#   - A city that is in the list below MUST match one of its province(s).
#   - A city that is NOT in the list is NOT rejected (we can't know every
#     town/village in Pakistan, and blocking a real customer from ordering
#     is worse than letting an unlisted small town through). Add missing
#     cities to CITIES_BY_PROVINCE below whenever you find one.
#   - A few names exist in more than one province (e.g. Khanpur) — those
#     accept any of their provinces (see EXTRA_PROVINCES).
#   - Islamabad accepts BOTH "Islamabad Capital Territory" and "Punjab",
#     because many province dropdowns only list the four provinces and
#     Islamabad customers would otherwise be blocked from ordering.

import re

PUNJAB = "Punjab"
SINDH = "Sindh"
KPK = "Khyber Pakhtunkhwa"
BALOCHISTAN = "Balochistan"
ICT = "Islamabad Capital Territory"
GB = "Gilgit-Baltistan"
AJK = "Azad Jammu and Kashmir"

PROVINCES = [PUNJAB, SINDH, KPK, BALOCHISTAN, ICT, GB, AJK]

# Different ways a province may be written/sent -> canonical name.
_PROVINCE_ALIASES = {
    "punjab": PUNJAB,
    "sindh": SINDH,
    "sind": SINDH,
    "khyber pakhtunkhwa": KPK,
    "khyber pakhtunkhawa": KPK,
    "khyber pukhtunkhwa": KPK,
    "khyber pakhtoonkhwa": KPK,
    "kpk": KPK,
    "kp": KPK,
    "nwfp": KPK,
    "balochistan": BALOCHISTAN,
    "baluchistan": BALOCHISTAN,
    "islamabad": ICT,
    "islamabad capital territory": ICT,
    "ict": ICT,
    "capital territory": ICT,
    "gilgit baltistan": GB,
    "gilgit-baltistan": GB,
    "gb": GB,
    "azad jammu and kashmir": AJK,
    "azad jammu kashmir": AJK,
    "azad kashmir": AJK,
    "ajk": AJK,
    "ak": AJK,
}

CITIES_BY_PROVINCE = {
    PUNJAB: [
        "lahore", "faisalabad", "rawalpindi", "multan", "gujranwala",
        "sialkot", "bahawalpur", "sargodha", "sheikhupura", "shaikhupura",
        "sheikhupurah", "jhang", "gujrat", "sahiwal", "kasur", "okara",
        "rahim yar khan", "ry khan", "dera ghazi khan", "dg khan",
        "d g khan", "chiniot", "kamoke", "hafizabad", "sadiqabad",
        "burewala", "khanewal", "mandi bahauddin", "muzaffargarh",
        "jhelum", "attock", "vehari", "pakpattan", "layyah", "lodhran",
        "narowal", "toba tek singh", "bhakkar", "chakwal", "khushab",
        "mianwali", "rajanpur", "nankana sahib", "sambrial", "daska",
        "wazirabad", "gojra", "jaranwala", "pattoki", "chishtian",
        "hasilpur", "kot addu", "taxila", "murree", "muridke",
        "ahmedpur east", "arifwala", "bahawalnagar", "haroonabad",
        "kamalia", "samundri", "mailsi", "shorkot", "lalamusa", "kharian",
        "dinga", "phalia", "sohawa", "pind dadan khan", "talagang",
        "fateh jang", "hassan abdal", "gujar khan", "kahuta", "wah cantt",
        "wah cantonment", "wah", "sillanwali", "bhalwal", "sangla hill",
        "shakargarh", "pasrur", "kot radha kishan", "chunian", "raiwind",
        "ferozewala", "mananwala", "sharaqpur", "kallar syedan",
        "chichawatni", "harappa", "renala khurd", "depalpur", "kabirwala",
        "jahanian", "shujabad", "jalalpur pirwala", "alipur", "uch sharif",
        "liaquatpur", "rojhan", "taunsa", "fort abbas", "minchinabad",
        "yazman", "dunyapur", "kahror pakka", "mian channu",
        "tandlianwala", "jauharabad", "quaidabad", "nowshera virkan",
        "kamra", "hazro", "jand", "pindi gheb", "lahore cantt",
        "sialkot cantt", "gujranwala cantt", "sargodha cantt",
        "multan cantt", "bahawalpur cantt", "chak jhumra", "sumandri",
        "ludhewala", "pir mahal", "faqirwali", "khanqah dogran",
    ],
    SINDH: [
        "karachi", "hyderabad", "sukkur", "larkana", "nawabshah",
        "shaheed benazirabad", "mirpur khas", "jacobabad", "shikarpur",
        "khairpur", "dadu", "thatta", "badin", "tando adam",
        "tando allahyar", "tando muhammad khan", "umerkot", "sanghar",
        "ghotki", "kashmore", "jamshoro", "matiari", "kotri", "mithi",
        "naushahro feroze", "moro", "kandiaro", "hala", "digri",
        "shahdadkot", "sehwan", "kunri", "pano aqil", "rohri", "daharki",
        "mehrabpur", "gambat", "ratodero", "mirpur mathelo", "tharparkar",
        "sujawal", "kambar", "warah", "tando jam", "khipro", "jati",
        "ranipur", "new saeedabad", "shahdadpur", "sakrand", "kot diji",
        "mehar", "johi", "bulri", "mirwah", "nagarparkar", "chachro",
        "islamkot", "diplo", "kandhkot", "thul", "garhi yasin",
    ],
    KPK: [
        "peshawar", "mardan", "mingora", "abbottabad", "kohat", "swabi",
        "nowshera", "charsadda", "dera ismail khan", "di khan", "d i khan",
        "bannu", "mansehra", "haripur", "swat", "lakki marwat", "tank",
        "chitral", "timergara", "karak", "hangu", "batkhela", "dargai",
        "malakand", "buner", "shangla", "kohistan", "battagram",
        "parachinar", "bajaur", "wana", "miranshah", "landi kotal",
        "jamrud", "havelian", "takht bhai", "pabbi", "topi", "tarbela",
        "dir", "upper dir", "lower dir", "kalam", "besham", "oghi",
        "balakot", "shabqadar", "ghazi", "khwazakhela", "matta",
        "daggar", "alpuri", "thakot", "kulachi", "paharpur",
        "peshawar cantt", "nowshera cantt", "kohat cantt",
    ],
    BALOCHISTAN: [
        "quetta", "gwadar", "turbat", "khuzdar", "sibi", "zhob", "chaman",
        "hub", "kalat", "loralai", "dera murad jamali", "dera allahyar",
        "nushki", "pishin", "mastung", "kharan", "panjgur", "lasbela",
        "uthal", "usta muhammad", "jaffarabad", "kohlu", "barkhan",
        "ziarat", "qila saifullah", "qila abdullah", "jhal magsi",
        "awaran", "washuk", "dera bugti", "sui", "harnai", "musakhel",
        "gandava", "pasni", "ormara", "dalbandin", "chagai", "taftan",
        "nasirabad", "mach", "bolan", "dhadar", "muslim bagh", "kachhi",
        "quetta cantt",
    ],
    ICT: [
        "islamabad",
    ],
    GB: [
        "gilgit", "skardu", "hunza", "chilas", "ghanche", "ghizer",
        "astore", "diamer", "nagar", "khaplu", "aliabad", "gahkuch",
        "karimabad", "shigar", "gupis",
    ],
    AJK: [
        "muzaffarabad", "mirpur", "kotli", "rawalakot", "bagh", "bhimber",
        "neelum", "sudhnoti", "hattian", "haveli", "poonch", "dadyal",
        "palandri", "athmuqam", "forward kahuta", "tatrinote",
    ],
}

# City names that legitimately exist in more than one province, plus the
# Islamabad leniency described at the top. These are ADDED to whatever
# CITIES_BY_PROVINCE already gives the city.
EXTRA_PROVINCES = {
    "islamabad": {PUNJAB},
    # Khanpur exists in both Punjab (Rahim Yar Khan) and KP (Haripur).
    "khanpur": {PUNJAB, KPK},
    # Nasirabad exists in both Balochistan and Sindh.
    "nasirabad": {SINDH},
}


def _norm(text):
    """lowercase, letters/spaces only, single spaces."""
    text = (text or "").lower()
    text = re.sub(r"[^a-z\s-]", " ", text).replace("-", " ")
    return re.sub(r"\s+", " ", text).strip()


def _build_city_map():
    city_map = {}
    for province, cities in CITIES_BY_PROVINCE.items():
        for city in cities:
            city_map.setdefault(_norm(city), set()).add(province)
    # Names that only appear through EXTRA_PROVINCES (e.g. Khanpur).
    for city, provinces in EXTRA_PROVINCES.items():
        city_map.setdefault(_norm(city), set()).update(provinces)
    return city_map


_CITY_MAP = _build_city_map()


def canonical_province(value):
    """Returns the canonical province name for whatever was sent, or None
    if it isn't a recognised province."""
    key = _norm(value)
    if not key:
        return None
    return _PROVINCE_ALIASES.get(key)


def provinces_for_city(city):
    """Set of provinces this city belongs to, or None if the city isn't in
    our list (unknown city — callers should NOT reject it)."""
    provinces = _CITY_MAP.get(_norm(city))
    return set(provinces) if provinces else None


def check_city_province(city, province):
    """Validates a typed city against the selected province.

    Returns (canonical_province, error_message):
      - (canonical, None)  -> fine (matches, or the city isn't in our list)
      - (None, "message")  -> reject with that message
    """
    canonical = canonical_province(province)
    if canonical is None:
        return None, (
            "Please select a valid province (Punjab, Sindh, Khyber "
            "Pakhtunkhwa, Balochistan, Islamabad Capital Territory, "
            "Gilgit-Baltistan or Azad Jammu and Kashmir)."
        )

    allowed = provinces_for_city(city)
    if allowed is not None and canonical not in allowed:
        expected = " or ".join(sorted(allowed))
        return None, (
            f"{city.strip().title()} is in {expected}, but {canonical} was "
            "selected. Please select the correct province for your city."
        )

    return canonical, None