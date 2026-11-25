"""Signed (parity) union-find.

A relation ``σ(a) = sign * σ(b)`` is stored for signs in {+1, -1}.  Every node's
sign is relative to a shared component root, so a whole component can be flipped
freely until an absolute anchor pins it down.  Inconsistent cycles surface as a
per-component contradiction flag instead of raising immediately; the caller
decides whether a contradiction matters (only touched components do).
"""

GROUND = ("__ground__",)


class ParityDSU:
    def __init__(self):
        self._parent = {}
        self._parity = {}  # sign(x) = parity[x] * sign(parent[x])
        self._contra = {}  # root -> bool

    # -- internals ---------------------------------------------------------
    def _add(self, x):
        if x not in self._parent:
            self._parent[x] = x
            self._parity[x] = 1
            self._contra[x] = False

    def find(self, x):
        self._add(x)
        root = x
        p = 1
        while self._parent[root] != root:
            p *= self._parity[root]
            root = self._parent[root]
        # path compression
        cur = x
        cur_p = 1
        while self._parent[cur] != cur:
            nxt = self._parent[cur]
            nxt_p = self._parity[cur]
            self._parent[cur] = root
            self._parity[cur] = p * cur_p
            cur_p *= nxt_p
            cur = nxt
        return root, p

    def add(self, x):
        self._add(x)

    def union(self, a, b, sign):
        """Assert sign(a) = sign * sign(b).  Returns False on contradiction."""
        ra, pa = self.find(a)
        rb, pb = self.find(b)
        if ra == rb:
            if pa == sign * pb:
                return True
            self._contra[ra] = True
            return False
        # sign(rb) = pa * sign * pb * sign(ra)
        self._parent[rb] = ra
        self._parity[rb] = pa * sign * pb
        merged = self._contra.get(ra, False) or self._contra.get(rb, False)
        self._contra[ra] = merged
        self._contra.pop(rb, None)
        return True

    def contradict(self, x):
        root, _ = self.find(x)
        return self._contra.get(root, False)

    def grounded(self, x):
        root, _ = self.find(x)
        ground_root, _ = self.find(GROUND)
        return root == ground_root

    def grounded_root(self):
        self._add(GROUND)
        return self.find(GROUND)[0]

    def sign_value(self, x):
        """Absolute sign when grounded, else None (and the flip factor)."""
        if not self.grounded(x):
            return None
        _, p = self.find(x)
        _, pg = self.find(GROUND)
        # sign(GROUND) == 1, sign(GROUND) = pg * sign(root) -> sign(root) = pg
        return p * pg

    def relative(self, x):
        """sign(x) / sign(root); None if ungrounded (root sign unknown)."""
        _, p = self.find(x)
        return p
