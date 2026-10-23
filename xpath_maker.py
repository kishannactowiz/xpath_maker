import re
import html as pyhtml  # for unescaping &alpha; &amp; ...
from lxml import html

# Attributes that identify a *kind of field* (stable across listings / pages).
STRONG_ATTRS = {"id", "itemprop", "property", "data-testid", "data-slot-id",
                "data-id", "name"}


# ---------------------------------------------------------------------------
#  Small helpers
# ---------------------------------------------------------------------------

def _q(value):
    """Quote a value safely for XPath."""
    return f"'{value}'" if "'" not in value else f'"{value}"'


def _lit(value):
    """XPath string literal that also works when value has ' and \"."""
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in parts) + ")"


def _clean(text):
    """Decode HTML entities and normalize whitespace."""
    text = pyhtml.unescape(text).replace("\xa0", " ")
    return " ".join(text.split())


def _match_text(text):
    """Normalize text and ignore a leading currency symbol."""
    text = _clean(text)
    return re.sub(r"^[\s$€£₹¥]+", "", text)


def _looks_generated(token):
    """True for random-looking class tokens such as cptQT7, czJRzd, k43YN."""
    if len(token) > 8:
        return False
    has_digit = any(c.isdigit() for c in token)
    has_alpha = any(c.isalpha() for c in token)
    if has_digit and has_alpha:
        return True
    return any(c.isupper() for c in token[1:]) and any(c.islower() for c in token)


def _attr_items(el, allow_id=True):
    """Return useful attributes, prioritizing stable semantic attributes."""
    priority = ["id", "itemprop", "property", "data-testid", "data-slot-id",
                "data-id", "name", "role", "aria-label", "type", "rel",
                "http-equiv", "title", "alt", "class"]

    extra = sorted(a for a in el.attrib
                   if isinstance(a, str) and a.startswith("data-")
                   and a not in priority)

    for attr in priority + extra:
        if attr == "id" and not allow_id:
            continue
        value = (el.get(attr) or "").strip()
        if not value:
            continue

        if attr == "class":
            tokens = [t for t in re.split(r"[\s_-]+", value) if t]
            # skip generated class names (cptQT7 ...), keep real ones (price, group)
            if any(_looks_generated(t) for t in tokens):
                continue
            if re.search(r"\b[a-fA-F0-9]{8,}\b", value):
                continue

        if attr in extra and (len(value) > 60 or value.isdigit()):
            continue

        yield attr, value


def _predicates(el):
    """Yield predicate lists like ["@id = 'x'"] (single, then pairs)."""
    singles = [f"@{a} = {_q(v)}" for a, v in _attr_items(el)]
    for s in singles:
        yield [s]
    for i in range(len(singles)):
        for j in range(i + 1, len(singles)):
            yield [singles[i], singles[j]]


def _first_pred(el):
    """First single attribute predicate of an element, e.g. @class = 'group'."""
    for preds in _predicates(el):
        return preds[0]
    return None


def _generic_pred(el, allow_id=False):
    """First attribute predicate that is shared by look-alike elements."""
    for a, v in _attr_items(el):
        if a == "id" and not allow_id:
            continue
        return f"@{a} = {_q(v)}"
    return None


def _text_exprs(el, max_len=80):
    """Yield contains(...) predicates using the first words of the element text."""
    words = " ".join(el.text_content().replace("\xa0", " ").split()).split(" ")
    snippet = ""
    for w in words:
        snippet = (snippet + " " + w).strip()
        if len(snippet) > max_len:
            return
        if len(snippet) >= 4:
            yield f"contains(normalize-space(.), {_lit(snippet)})"


def _label_exprs(el, max_len=40):
    """
    For short texts with a changing number, like "93 views" or "7 favorites",
    use the stable label word:  contains(normalize-space(.), 'views')
    """
    text = " ".join(el.text_content().replace("\xa0", " ").split())
    if not re.search(r"\d", text) or len(text) > max_len:
        return
    seen = set()
    for w in re.findall(r"[^\W\d_]{3,}", text):
        if w not in seen:
            seen.add(w)
            yield f"contains(normalize-space(.), {_lit(w)})"


# ---------------------------------------------------------------------------
#  Building blocks for the two XPaths
# ---------------------------------------------------------------------------

def _unique_expr(el, scope, scope_prefix, allow_tag_only=True, use_text=False,
                 use_label=False):
    """
    Find a `tag[@attr = 'v']` expression that matches ONLY `el`
    inside `scope` (the whole doc or an ancestor element).
    """
    tag = el.tag
    if allow_tag_only:
        if scope.xpath(f"{scope_prefix}{tag}") == [el]:
            return tag
    for preds in _predicates(el):
        expr = tag + "".join(f"[{p}]" for p in preds)
        if scope.xpath(f"{scope_prefix}{expr}") == [el]:
            return expr
    if use_text:
        for pred in _text_exprs(el):
            expr = f"{tag}[{pred}]"
            if scope.xpath(f"{scope_prefix}{expr}") == [el]:
                return expr
    if use_label:
        for pred in _label_exprs(el):
            expr = f"{tag}[{pred}]"
            if scope.xpath(f"{scope_prefix}{expr}") == [el]:
                return expr
    return None


def _qualified(el, root, max_markers=60, use_label=True, strict_only=False):
    """
    Pick `el` out of many look-alikes by using a unique "marker" element
    (e.g. a heading) that lives inside a nearby parent:

      //div[@class = 'group'][.//h3[@data-id = '#Dimensions']]
          //p[@class = 'plp-attr-value-img']//img
    """
    chain = list(el.iterancestors())
    chain_set = set(chain)
    own_pred = _first_pred(el)
    own = el.tag + (f"[{own_pred}]" if own_pred else "")
    own_variants = [own]
    if use_label and not own_pred:
        own_variants += [f"{el.tag}[{p}]" for p in _label_exprs(el)]
    cache = {}
    best = None  # (match_count, xpath)

    for i, anc in enumerate(chain):
        if anc.tag in ("html", "body"):
            continue
        base_pred = _first_pred(anc)
        if not base_pred:
            continue
        base = f"{anc.tag}[{base_pred}]"

        mid = None
        for m in chain[:i]:
            mp = _first_pred(m)
            if mp:
                mid = f"{m.tag}[{mp}]"
                break
        inners = ([f"{mid}//{o}" for o in own_variants] if mid else []) + own_variants

        tested = 0
        for d in anc.iterdescendants():
            if not isinstance(d.tag, str) or d is el or d in chain_set:
                continue
            if el in d.iterancestors() or not _first_pred(d):
                continue
            if d not in cache:
                cache[d] = _unique_expr(d, root, "//", allow_tag_only=False)
            marker = cache[d]
            if not marker:
                continue
            tested += 1
            if tested > max_markers:
                break
            for inner in inners:
                xp = f"//{base}[.//{marker}]//{inner}"
                res = root.xpath(xp)
                if el in res:
                    if len(res) == 1:
                        return xp
                    if best is None or len(res) < best[0]:
                        best = (len(res), xp)
    return best[1] if best and not strict_only else None


def _unique_only(el, root, anchor=True, use_text=False, use_label=True):
    """
    An attribute-based XPath that selects ONLY `el` (no positions, no text
    of the search). Returns None when the page has no way to single it out.
    """
    tries = []
    if not anchor:
        e = _unique_expr(el, root, "//", use_text=use_text, use_label=use_label)
        if e:
            tries.append("//" + e)

    ancestors = list(el.iterancestors())
    ancestors.sort(key=lambda a: 0 if a.get("id") else 1)   # ids first
    for anc in ancestors:
        ae = _unique_expr(anc, root, "//", allow_tag_only=False)
        if not ae:
            continue
        inner = _unique_expr(el, anc, ".//", allow_tag_only=not anchor,
                             use_text=use_text, use_label=use_label) \
            or _unique_expr(el, anc, ".//", use_text=use_text, use_label=use_label)
        if inner:
            tries.append(f"//{ae}//{inner}")

    q = _qualified(el, root, use_label=use_label, strict_only=True)
    if q:
        tries.append(q)
    e = _unique_expr(el, root, "//", use_text=use_text, use_label=use_label)
    if e:
        tries.append("//" + e)

    for xp in tries:
        try:
            if root.xpath(xp) == [el]:
                return xp
        except Exception:
            pass
    return None


def _reusable_xpath(el, root, sibling=True, use_label=True):
    """
    Reusable XPath: the same *field* in every listing / card, built only from
    attributes (no text, no page-wide position):

        //div[@data-slot-id = 'EdlpPrice']//span[1]
        //span[@itemprop = 'price']

    sibling=True  -> if look-alike siblings exist (price / old price), tell
                     them apart by a label word ("views") or, if there is
                     none, by the position inside the SAME parent: span[1].
    """
    own_pred = _generic_pred(el)
    own = el.tag + (f"[{own_pred}]" if own_pred else "")
    strong = bool(own_pred) and own_pred[1:].split(" ", 1)[0] in STRONG_ATTRS

    cands = []
    if strong:
        cands.append(f"//{own}")
    for anc in el.iterancestors():
        if anc.tag in ("html", "body"):
            break
        ap = _generic_pred(anc)
        if ap:
            cands.append(f"//{anc.tag}[{ap}]//{own}")
            break
    if own_pred and not strong:
        cands.append(f"//{own}")

    parent = el.getparent()
    for xp in cands:
        res = root.xpath(xp)
        if el not in res:
            continue
        # A strong attribute (data-testid="product-card", itemprop="price" ...)
        # already names the field -> every match is wanted, never pick by position.
        if not sibling or (strong and xp == f"//{own}"):
            return xp

        # Tell look-alike siblings apart (price / old price, views / favorites)
        # ONLY when the same small block repeats (listing cards). A long run of
        # siblings in one grid is a list of items: keep them all.
        groups = {}
        for c in res:
            groups.setdefault(c.getparent(), []).append(c)
        sibs = groups.get(parent, [])
        repeated_blocks = len(groups) >= 2 and max(len(g) for g in groups.values()) <= 4
        if len(sibs) > 1 and repeated_blocks:
            if use_label:
                for pred in _label_exprs(el):
                    cand = f"{xp}[{pred}]"
                    r = root.xpath(cand)
                    if el in r and sum(1 for c in r if c.getparent() is parent) == 1:
                        return cand
            return f"{xp}[{sibs.index(el) + 1}]"
        return xp
    return None


def _text_predicate(el, text):
    """normalize-space(.) = 'full text'   (or contains(...) for part of a text)."""
    full = _clean(el.text_content())
    expr = "normalize-space(translate(., '\u00a0', ' '))" \
        if "\xa0" in el.text_content() else "normalize-space(.)"
    if _match_text(full) == _match_text(text):
        return f"{expr} = {_lit(full)}"
    return f"contains({expr}, {_lit(_clean(text))})"


def _xpath_options(el, root, text, anchor=True, use_text=False, index=False,
                   use_label=True):
    """
    Returns [exact, reusable] for one element:

      exact     selects ONLY this element. Attribute based when the page allows
                it, otherwise: reusable structure + an exact-text predicate.
      reusable  selects the same field in every listing.
    """
    reusable = _reusable_xpath(el, root, use_label=use_label)

    exact = _unique_only(el, root, anchor, use_text, use_label)
    if not exact:
        base = _reusable_xpath(el, root, sibling=False, use_label=use_label)
        if base:
            exact = f"{base}[{_text_predicate(el, text)}]"
        elif index or True:
            exact = el.getroottree().getpath(el)   # nothing else works

    out = []
    for xp in (exact, reusable):
        if xp and xp not in out:
            out.append(xp)
    return out


# ---------------------------------------------------------------------------
#  Search one text
# ---------------------------------------------------------------------------

def _search_one(root, text, exact, with_text, anchor, use_text, index, limit,
                use_label):
    """XPaths for ONE text on an already parsed page: [exact, reusable]."""
    text = _clean(text)

    def N(x):
        return f"normalize-space(translate({x}, '\u00a0', ' '))"

    def cond(x):
        return f"{N(x)} = $t" if exact else f"contains({N(x)}, $t)"

    text_q = f"//*[text()[{cond('.')}]]"
    deep_q = f"//*[{cond('.')}][not(*[{cond('.')}])]"   # text split by child tags
    attr_q = f"//@*[{cond('.')}]"

    groups = []   # (rank_key, [xpaths])

    # 1) text inside tags; 2) if none, text split across child tags
    elements = root.xpath(text_q, t=text) or root.xpath(deep_q, t=text)

    # keep the smallest matching elements, not their broad parent containers
    smallest = []
    for el in elements:
        if not isinstance(el.tag, str) or el.tag in ("script", "style"):
            continue
        inner_match = any(
            isinstance(c.tag, str)
            and _match_text(text) in _match_text(c.text_content())
            for c in el.iterdescendants())
        if not inner_match:
            smallest.append(el)

    suffix = "//text()" if with_text else ""
    for el in smallest:
        xps = [x + suffix for x in
               _xpath_options(el, root, text, anchor, use_text, index, use_label)]
        exact_hit = _match_text(el.text_content()) == _match_text(text)
        in_head = any(a.tag == "head" for a in el.iterancestors())
        key = (not exact_hit, in_head, xps[0].startswith("/html"), False,
               -sum(xps[0].count("@" + k) for k in
                    ("data-testid", "data-slot-id", "itemprop", "property", "id")),
               len(xps[0]))
        groups.append((key, xps))

    # 3) attribute values (images, links, meta tags, ...)
    for attr in root.xpath(attr_q, t=text):
        el = attr.getparent()
        if el is None:
            continue
        name, value = attr.attrname, str(attr)
        exact_hit = _clean(value) == text

        uniq = _unique_only(el, root, anchor, use_text, use_label)
        if uniq:
            exact_xp = f"{uniq}/@{name}"
        else:
            base = _reusable_xpath(el, root, sibling=False, use_label=use_label)
            pred = (f"@{name} = {_lit(value)}" if exact_hit
                    else f"contains(@{name}, {_lit(text)})")
            exact_xp = f"{base}[{pred}]/@{name}" if base \
                else f"{el.getroottree().getpath(el)}/@{name}"
        reuse = _reusable_xpath(el, root, use_label=use_label)

        xps = [exact_xp]
        if reuse:
            xps.append(f"{reuse}/@{name}")
        in_head = any(a.tag == "head" for a in el.iterancestors())
        key = (not exact_hit, in_head, exact_xp.startswith("/html"), True,
               0, len(exact_xp))
        groups.append((key, xps))

    results = []
    for _, xps in sorted(groups, key=lambda g: g[0]):
        for xp in xps:
            if xp not in results:
                results.append(xp)
    return results[:limit] if limit else results


def xpath_maker(html_file, text, exact=False, with_text=False, anchor=True,
                encoding="utf-8", use_text=False, index=False, limit=2,
                use_label=True):
    """
    Return dynamic XPaths for `text`, best match first:

      1st  EXACT     selects only the element holding this text
      2nd  REUSABLE  selects the same field in every listing (all prices ...)

    `text` can be:
      * one string                     -> list of XPaths
      * a list of strings              -> {text: [xpaths]}
      * a dict {name: text}            -> {name: [xpaths]}

    HTML entities in the text (&alpha; &amp; ...) are decoded first.
    The file is parsed only once, even for many inputs.

    exact     : True -> value/text must equal `text` (default: contains)
    with_text : True -> append //text() to text matches
    anchor    : True -> prefer a parent in the exact XPath (e.g. div[@id = '..'])
    limit     : XPaths returned per input (default 2 = exact + reusable)
    use_label : "93 views" is told apart from "7 favorites" by its label word
    """
    root = html.parse(html_file, html.HTMLParser(encoding=encoding)).getroot()
    opts = (exact, with_text, anchor, use_text, index, limit, use_label)

    if isinstance(text, str):
        return _search_one(root, text, *opts)
    if isinstance(text, dict):
        return {name: _search_one(root, t, *opts) for name, t in text.items()}
    return {t: _search_one(root, t, *opts) for t in text}


# ---------------------------------------------------------------------------
#  Key / value pairs  ->  rows XPath + key XPath + value XPath
# ---------------------------------------------------------------------------

def _find_elements(root, text, exact=True):
    """Elements whose text equals / contains `text` (exact falls back to contains)."""
    def N(x):
        return f"normalize-space(translate({x}, '\u00a0', ' '))"

    def search(ex):
        c = f"{N('.')} = $t" if ex else f"contains({N('.')}, $t)"
        found = root.xpath(f"//*[text()[{c}]]", t=text) or \
            root.xpath(f"//*[{c}][not(*[{c}])]", t=text)
        return [e for e in found
                if isinstance(e.tag, str) and e.tag not in ("script", "style")]

    return (search(True) if exact else []) or search(False)


def _lca(a, b):
    in_b = {b, *b.iterancestors()}
    for x in (a, *a.iterancestors()):
        if x in in_b:
            return x


def _rows_xpath(row, root):
    """XPath that matches ALL look-alike rows: //div[..]//table[..]//tr[..]"""
    pred = _generic_pred(row)
    row_step = row.tag + (f"[{pred}]" if pred else "")
    parts = []
    for anc in row.iterancestors():
        if anc.tag in ("html", "body"):
            break
        ap = _generic_pred(anc, allow_id=True)
        if not ap:
            continue
        parts.append(f"{anc.tag}[{ap}]")
        if ap.startswith("@id") or len(parts) >= 2:
            break
    xp = "//" + "//".join(list(reversed(parts)) + [row_step])
    return xp if row in root.xpath(xp) else "//" + row_step


def _relative_xpath(el, row, other):
    """
    XPath from `row` down to `el` (without //text()), e.g.
      ./td[@class = 'plp-table-value']//span[@itemprop = 'value']
    It must find `el` but never the `other` cell of the same row.
    """
    nodes = []
    cur = el
    while cur is not row:
        nodes.append(cur)
        cur = cur.getparent()
    nodes.reverse()  # top -> el

    def step(n, pos=False):
        pr = _generic_pred(n)
        st = n.tag + (f"[{pr}]" if pr else "")
        if pos:
            sibs = [x for x in n.getparent() if x.tag == n.tag]
            st += f"[{sibs.index(n) + 1}]"
        return st

    def build(ns, sep, pos_first=False):
        out = [step(n, pos_first and k == 0) for k, n in enumerate(ns)]
        lead = "./" if ns[0].getparent() is row else ".//"
        return lead + sep.join(out)

    keep = [n for n in nodes if n is el or _generic_pred(n)]
    tries = [build(keep, "//"), build(nodes, "/"), build(nodes, "/", pos_first=True)]
    for xp in tries:
        res = row.xpath(xp)
        if el in res and other not in res:
            return xp
    return tries[-1]


def xpath_pair_maker(html_file, key_text, value_text, exact=True, encoding="utf-8"):
    """
    Give one example key and its value (as shown on the page) and get back
    XPaths that extract ALL key/value pairs of that table / list:

      {"rows":  "//div[@class = 'group']//table[..]//tr[@itemprop = '..']",
       "key":   "./td[@itemprop = 'name']//text()",
       "value": "./td[@class = '..']//span[@itemprop = 'value']//text()"}
    """
    root = html.parse(html_file, html.HTMLParser(encoding=encoding)).getroot()
    keys = _find_elements(root, _clean(key_text), exact)
    vals = _find_elements(root, _clean(value_text), exact)

    best = None  # (depth, key_el, value_el, row)
    for k in keys:
        for v in vals:
            row = _lca(k, v)
            if row is None or row is k or row is v:
                continue
            if row.tag in ("html", "body"):
                continue
            depth = len(list(row.iterancestors()))
            if best is None or depth > best[0]:
                best = (depth, k, v, row)
    if not best:
        return None

    _, k, v, row = best
    rows = _rows_xpath(row, root)
    return {
        "rows": rows,
        "key": _relative_xpath(k, row, v) + "//text()",
        "value": _relative_xpath(v, row, k) + "//text()",
        "row_count": len(root.xpath(rows)),
    }


def extract_pairs(html_file, spec, encoding="utf-8", sep=" / "):
    """Apply the XPaths from xpath_pair_maker (same logic as a scrapy loop)."""
    root = html.parse(html_file, html.HTMLParser(encoding=encoding)).getroot()
    result = {}
    for row in root.xpath(spec["rows"]):
        key = " ".join(row.xpath(spec["key"])).strip()
        values = [x.strip() for x in row.xpath(spec["value"]) if x.strip()]
        value = sep.join(values)
        if key and value:
            result[key] = value
    return result


if __name__ == "__main__":
    for xp in xpath_maker("page.html", "Hello World", with_text=True):
        print(xp)
