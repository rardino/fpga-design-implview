"""LUT contents: the INIT of a LUT1..LUT6 cell as a boolean equation, and
the physical (BEL) truth table after LUT pin swapping.

INIT is logical: bit k is the output for the inputs I0..In-1 that spell k in
binary, I0 the least significant bit.
"""

import itertools
import re

_VERILOG = re.compile(r"^\s*(\d+)?'([bhdBHD])([0-9a-fA-F_xXzZ]+)\s*$")


def parse_init(s):
    """(value, width) of a Vivado INIT string: 8'hB8, 64'h..., 0x.., decimal."""
    s = s.strip()
    m = _VERILOG.match(s)
    if m:
        base = {"b": 2, "h": 16, "d": 10}[m.group(2).lower()]
        v = int(m.group(3).replace("_", ""), base)
        w = int(m.group(1)) if m.group(1) else max(v.bit_length(), 1)
        return v, w
    if s.lower().startswith("0x"):
        v = int(s, 16)
        return v, max(4 * (len(s) - 2), 1)
    v = int(s)
    return v, max(v.bit_length(), 1)


def lut_inputs(ref):
    """Inputs of a LUT1..LUT6 REF_NAME, None for anything else (SRL, RAMD, CFGLUT)."""
    m = re.fullmatch(r"LUT([1-6])", ref or "")
    return int(m.group(1)) if m else None


def minterms(init, n):
    return [k for k in range(1 << n) if (init >> k) & 1]


def _combine(a, b):
    """Merge two implicants (value, mask) that differ in one cared bit."""
    if a[1] != b[1]:
        return None
    d = a[0] ^ b[0]
    if d and not d & (d - 1):
        return (a[0] & ~d, a[1] | d)
    return None


def prime_implicants(ones, n):
    cur = {(m, 0) for m in ones}
    primes = set()
    while cur:
        nxt, used = set(), set()
        lst = sorted(cur)
        for a, b in itertools.combinations(lst, 2):
            c = _combine(a, b)
            if c is not None:
                nxt.add(c)
                used.add(a)
                used.add(b)
        primes |= cur - used
        cur = nxt
    return primes


def _covers(imp, m):
    v, mask = imp
    return (m & ~mask) == v


def minimize(ones, n):
    """A small sum of products: essential primes, then greedy cover."""
    if not ones:
        return []
    primes = sorted(prime_implicants(ones, n), key=lambda p: (-bin(p[1]).count("1"), p))
    left = set(ones)
    chosen = []
    for m in sorted(ones):
        cov = [p for p in primes if _covers(p, m)]
        if len(cov) == 1 and cov[0] not in chosen:
            chosen.append(cov[0])
    for p in chosen:
        left -= {m for m in left if _covers(p, m)}
    while left:
        best = max(primes, key=lambda p: (sum(_covers(p, m) for m in left), bin(p[1]).count("1")))
        chosen.append(best)
        left -= {m for m in left if _covers(best, m)}
    return chosen


def equation(init, n, names=None):
    """Sum of products of a LUT INIT, in names (default I0..In-1).
    ~ not, & and, | or; constants 0 and 1."""
    names = names or [f"I{i}" for i in range(n)]
    ones = minterms(init, n)
    if not ones:
        return "0"
    if len(ones) == 1 << n:
        return "1"
    terms = []
    for v, mask in minimize(ones, n):
        lits = [("" if (v >> i) & 1 else "~") + names[i] for i in range(n) if not (mask >> i) & 1]
        terms.append(" & ".join(lits) if lits else "1")
    if len(terms) > 1:
        terms = [f"({t})" if " & " in t else t for t in terms]
    return " | ".join(terms)


def eval_sop(expr, n, k, names=None):
    """Value of an equation() string for input vector k (testing aid)."""
    names = names or [f"I{i}" for i in range(n)]
    env = {nm: bool((k >> i) & 1) for i, nm in enumerate(names)}
    if expr in ("0", "1"):
        return expr == "1"
    for term in expr.split(" | "):
        term = term.strip("()")
        ok = True
        for lit in term.split(" & "):
            neg = lit.startswith("~")
            val = env[lit.lstrip("~")]
            if val == neg:
                ok = False
                break
        if ok:
            return True
    return False


def parse_pinmap(s):
    """"I0:A6 I1:A4" -> {0: 6, 1: 4} (logical input -> physical A1..A6)."""
    out = {}
    for tok in s.split():
        lg, _, ph = tok.partition(":")
        m1, m2 = re.fullmatch(r"I(\d)", lg), re.search(r"A(\d)$", ph)
        if m1 and m2:
            out[int(m1.group(1))] = int(m2.group(1))
    return out


def physical_init(init, n, pinmap, nphys=6):
    """Truth table over the BEL pins A1..A<nphys> (bit k: A1 the LSB) of a
    logical LUT whose input Ii sits on pin A<pinmap[i]>. Unused pins are
    don't-care, the function does not depend on them."""
    out = 0
    for k in range(1 << nphys):
        lk = 0
        for i in range(n):
            a = pinmap.get(i)
            if a and (k >> (a - 1)) & 1:
                lk |= 1 << i
        if (init >> lk) & 1:
            out |= 1 << k
    return out
