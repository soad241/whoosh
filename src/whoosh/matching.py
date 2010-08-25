#===============================================================================
# Copyright 2010 Matt Chaput
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#    http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#===============================================================================

from bisect import bisect_left, bisect_right, insort

from whoosh.util import make_binary_tree


"""
This module contains "matcher" classes. Matchers deal with posting lists. The
most basic matcher, which reads the list of postings for a term, will be
provided by the backend implementation (for example,
``whoosh.filedb.filepostings.FilePostingReader``). The classes in this module
provide additional functionality, such as combining the results of two
matchers, or modifying the results of a matcher.

You do not need to deal with the classes in this module unless you need to
write your own Matcher implementation to provide some new functionality. These
classes are not instantiated by the user. They are usually created by a
:class:`~whoosh.query.Query` object's ``matcher()`` method, which returns the
appropriate matcher to implement the query (for example, the ``Or`` query's
``matcher()`` method returns a ``UnionMatcher`` object).

Certain backends support "quality" optimizations. These backends have the
ability to skip ahead if it knows the current block of postings can't
contribute to the top N documents. If the matcher tree and backend support
these optimizations, the matcher's ``supports_quality()`` method will return
``True``.
"""


class ReadTooFar(Exception):
    """Raised when next() or skip_to() is called on an inactive matchers.
    """


class NoQualityAvailable(Exception):
    """Raised when quality methods are called on a matcher that does not
    support quality-based optimizations.
    """


# Span class

class Span(object):
    __slots__ = ("start", "end")
    
    def __init__(self, start, end=None):
        if end is None:
            end = start
        assert start <= end
        self.start = start
        self.end = end

    def __repr__(self):
        return "<%d-%d>" % (self.start, self.end)

    def __eq__(self, span):
        return self.start == span.start and self.end == span.end
    
    def __ne__(self, span):
        return self.start != span.start or self.end != span.end
    
    def __lt__(self, span):
        return self.start < span.start
    
    def __gt__(self, span):
        return self.start > span.start
    
    def __hash__(self):
        return hash((self.start, self.end))
    
    @classmethod
    def merge(cls, spans):
        """Merges overlapping and touches spans in the given list of spans.
        
        Note that this modifies the original list.
        
        >>> spans = [Span(1,2), Span(3)]
        >>> Span.merge(spans)
        >>> spans
        [<1-3>]
        """
        
        i = 0
        while i < len(spans)-1:
            here = spans[i]
            j = i + 1
            while j < len(spans):
                there = spans[j]
                if there.start > here.end + 1:
                    break
                if here.touches(there) or here.overlaps(there):
                    here = here.to(there)
                    spans[i] = here
                    del spans[j]
                else:
                    j += 1
            i += 1
        return spans
    
    def to(self, span):
        return self.__class__(min(self.start, span.start), max(self.end, span.end))
    
    def overlaps(self, span):
        return ((self.start >= span.start and self.start <= span.end)
                or (self.end >= span.start and self.end <= span.end)
                or (span.start >= self.start and span.start <= self.end)
                or (span.end >= self.start and span.end <= self.end))
    
    def surrounds(self, span):
        return self.start < span.start and self.end > span.end
    
    def is_within(self, span):
        return self.start >= span.start and self.end <= span.end
    
    def is_before(self, span):
        return self.end < span.start
    
    def is_after(self, span):
        return self.start > span.end
    
    def touches(self, span):
        return self.start == span.end + 1 or self.end == span.start - 1
    
    def distance_to(self, span):
        if self.overlaps(span):
            return 0
        elif self.is_before(span):
            return span.start - self.end
        elif self.is_after(span):
            return self.start - span.end


# Matchers

class Matcher(object):
    """Base class for all matchers.
    """
    
    def is_active(self):
        """Returns True if this matcher is still "active", that is, it has not
        yet reached the end of the posting list.
        """
        
        raise NotImplementedError
    
    def replace(self):
        """Returns a possibly-simplified version of this matcher. For example,
        if one of the children of a UnionMatcher is no longer active, calling
        this method on the UnionMatcher will return the other child.
        """
        
        return self
    
    def copy(self):
        """Returns a copy of this matcher.
        """
        
        raise NotImplementedError
    
    def depth(self):
        """Returns the depth of the tree under this matcher, or 0 if this
        matcher does not have any children.
        """
        
        return 0
    
    def supports_quality(self):
        """Returns True if this matcher supports the use of ``quality`` and
        ``block_quality``.
        """
        
        return False
    
    def quality(self):
        """Returns a quality measurement of the current posting, according to
        the current weighting algorithm. Raises ``NoQualityAvailable`` if the
        matcher or weighting do not support quality measurements.
        """
        
        raise NoQualityAvailable
    
    def block_quality(self):
        """Returns a quality measurement of the current block of postings,
        according to the current weighting algorithm. Raises
        ``NoQualityAvailable`` if the matcher or weighting do not support
        quality measurements.
        """
        
        raise NoQualityAvailable
    
    def id(self):
        """Returns the ID of the current posting.
        """
        
        raise NotImplementedError
    
    def all_ids(self):
        """Returns a generator of all IDs in the matcher.
        
        What this method returns for a matcher that has already read some
        postings (whether it only yields the remaining postings or all postings
        from the beginning) is undefined, so it's best to only use this method
        on fresh matchers.
        """
        
        i = 0
        while self.is_active():
            yield self.id()
            self.next()
            i += 1
            if i == 10:
                self = self.replace()
                i = 0
                
    def all_items(self):
        """Returns a generator of all (ID, encoded value) pairs in the matcher.
        
        What this method returns for a matcher that has already read some
        postings (whether it only yields the remaining postings or all postings
        from the beginning) is undefined, so it's best to only use this method
        on fresh matchers.
        """
        
        i = 0
        while self.is_active():
            yield (self.id(), self.value())
            self.next()
            i += 1
            if i == 10:
                self = self.replace()
                i = 0
    
    def items_as(self, astype):
        """Returns a generator of all (ID, decoded value) pairs in the matcher.
        
        What this method returns for a matcher that has already read some
        postings (whether it only yields the remaining postings or all postings
        from the beginning) is undefined, so it's best to only use this method
        on fresh matchers.
        """
        
        while self.is_active():
            yield (self.id(), self.value_as(astype))
    
    def value(self):
        """Returns the encoded value of the current posting.
        """
        
        raise NotImplementedError
    
    def value_as(self, astype):
        """Returns the value(s) of the current posting as the given type.
        """
        
        raise NotImplementedError("value_as not implemented in %s" % self.__class__)
    
    def positions(self):
        return self.value_as("positions")
    
    def spans(self):
        return [Span(pos) for pos in self.positions()]
    
    def skip_to(self, id):
        """Moves this matcher to the first posting with an ID equal to or
        greater than the given ID.
        """
        
        while self.is_active() and self.id() < id:
            self.next()
    
    def skip_to_quality(self, minquality):
        """Moves this matcher to the next block with greater than the given
        minimum quality value.
        """
        
        raise NotImplementedError
    
    def next(self):
        """Moves this matcher to the next posting.
        """
        
        raise NotImplementedError
    
    def weight(self):
        """Returns the weight of the current posting.
        """
        
        return self.value_as("weight")
    
    def score(self):
        """Returns the score of the current posting.
        """
        
        raise NotImplementedError
    

class NullMatcher(Matcher):
    """Matcher with no postings which is never active.
    """
    
    def is_active(self):
        return False
    
    def all_ids(self):
        return []


class ListMatcher(Matcher):
    """Synthetic matcher backed by a list of IDs.
    """
    
    def __init__(self, ids, position=0, weight=1.0):
        self._ids = ids
        self._i = position
        self._weight = weight
    
    def __repr__(self):
        return "%s(%r, %d)" % (self.__class__.__name__, self._ids, self._i)
    
    def is_active(self):
        return self._i < len(self._ids)
    
    def copy(self):
        return self.__class__(self._ids[:], self._i, self._weight)
    
    def id(self):
        return self._ids[self._i]
    
    def all_ids(self):
        return iter(self._ids)
    
    def next(self):
        self._i += 1
        
    def weight(self):
        return self._weight
    
    def score(self):
        return self._weight


class WrappingMatcher(Matcher):
    """Base class for matchers that wrap sub-matchers.
    """
    
    def __init__(self, child, boost=1.0):
        self.child = child
        self.boost = boost
    
    def __repr__(self):
        return "%s(%r, boost=%s)" % (self.__class__.__name__, self.child, self.boost)
    
    def copy(self):
        kwargs = {}
        if hasattr(self, "boost"):
            kwargs["boost"] = self.boost
        return self.__class__(self.child.copy(), **kwargs)
    
    def depth(self):
        return 1 + self.child.depth()
    
    def replace(self):
        r = self.child.replace()
        if not r.is_active(): return NullMatcher()
        if r is not self.child: return self.__class__(r)
        return self
    
    def id(self):
        return self.child.id()
    
    def all_ids(self):
        return self.child.all_ids()
    
    def is_active(self):
        return self.child.is_active()
    
    def value(self):
        return self.child.value()
    
    def value_as(self, astype):
        return self.child.value_as(astype)
    
    def positions(self):
        return self.child.positions()
    
    def skip_to(self, id):
        return self.child.skip_to(id)
    
    def next(self):
        self.child.next()
    
    def supports_quality(self):
        return self.child.supports_quality()
    
    def skip_to_quality(self, minquality):
        return self.child.skip_to_quality(minquality/self.boost)
    
    def quality(self):
        return self.child.quality() * self.boost
    
    def block_quality(self):
        return self.child.block_quality() * self.boost
    
    def weight(self):
        return self.child.weight() * self.boost
    
    def score(self):
        return self.child.score() * self.boost
    

class MultiMatcher(Matcher):
    """Serializes the results of a list of sub-matchers.
    """
    
    def __init__(self, matchers, idoffsets, current=0):
        self.matchers = matchers
        self.offsets = idoffsets
        self.current = current
        self._next_matcher()
    
    def __repr__(self):
        return "%s(%r, %r, current=%s)" % (self.__class__.__name__,
                                           self.matchers, self.offsets,
                                           self.current)
    
    def is_active(self):
        return self.current < len(self.matchers)
    
    def _next_matcher(self):
        matchers = self.matchers
        while self.current < len(matchers) and not matchers[self.current].is_active():
            self.current += 1
        
    def copy(self):
        return self.__class__([mr.copy() for mr in self.matchers[self.current:]],
                              self.offsets[self.current:], current=self.current)
    
    def depth(self):
        if self.is_active():
            return 1 + max(mr.depth() for mr in self.matchers[self.current:])
        else:
            return 0
    
    def replace(self):
        if not self.is_active():
            return NullMatcher()
        # TODO: Possible optimization: if the last matcher is current, replace
        # this with the last matcher, but wrap it with a matcher that adds the
        # offset. Have to check whether that's actually faster, though.
        return self
    
    def id(self):
        current = self.current
        return self.matchers[current].id() + self.offsets[current]
    
    def all_ids(self):
        offsets = self.offsets
        for i, mr in enumerate(self.matchers):
            for id in mr.all_ids():
                yield id + offsets[i]
    
    def value(self):
        return self.matchers[self.current].value()
    
    def next(self):
        if not self.is_active(): raise ReadTooFar
        
        self.matchers[self.current].next()
        if not self.matchers[self.current].is_active():
            self._next_matcher()
        
    def skip_to(self, id):
        if not self.is_active(): raise ReadTooFar
        if id <= self.id(): return
        
        matchers = self.matchers
        offsets = self.offsets
        r = False
        
        while self.current < len(matchers) and id > self.id():
            mr = matchers[self.current]
            sr = mr.skip_to(id - offsets[self.current])
            r = sr or r
            if mr.is_active():
                break
            
            self.current += 1
            
        return r
    
    def supports_quality(self):
        return all(mr.supports_quality() for mr in self.matchers[self.current:])
    
    def quality(self):
        return self.matchers[self.current].quality()
    
    def block_quality(self):
        return self.matchers[self.current].block_quality()
    
    def weight(self):
        return self.matchers[self.current].weight()
    
    def score(self):
        return self.matchers[self.current].score()


class ExcludeMatcher(WrappingMatcher):
    """Excludes a list of IDs from the postings returned by the wrapped
    matcher.
    """
    
    def __init__(self, child, excluded, boost=1.0):
        super(ExcludeMatcher, self).__init__(child)
        self.excluded = excluded
        self.boost = boost
        self._find_next()
    
    def __repr__(self):
        return "%s(%r, %r, boost=%s)" % (self.__class__.__name__, self.child,
                                         self.excluded, self.boost)
    
    def copy(self):
        return self.__class__(self.child.copy(), self.excluded, boost=self.boost)
    
    def _find_next(self):
        child = self.child
        excluded = self.excluded
        r = False
        while child.is_active() and child.id() in excluded:
            r = child.next() or r
        return r
    
    def next(self):
        self.child.next()
        self._find_next()
        
    def skip_to(self, id):
        self.child.skip_to(id)
        self._find_next()
        
    def all_ids(self):
        excluded = self.excluded
        return (id for id in self.child.all_ids() if id not in excluded)
    
    def all_items(self):
        excluded = self.excluded
        return (item for item in self.child.all_items()
                if item[0] not in excluded)


class BiMatcher(Matcher):
    """Base class for matchers that combine the results of two sub-matchers in
    some way.
    """
    
    def __init__(self, a, b):
        super(BiMatcher, self).__init__()
        self.a = a
        self.b = b

    def __repr__(self):
        return "%s(%r, %r)" % (self.__class__.__name__, self.a, self.b)

    def copy(self):
        return self.__class__(self.a.copy(), self.b.copy())

    def depth(self):
        return 1 + max(self.a.depth(), self.b.depth())

    def skip_to(self, id):
        if not self.is_active(): raise ReadTooFar
        ra = self.a.skip_to(id)
        rb = self.b.skip_to(id)
        return ra or rb
        
    def supports_quality(self):
        return self.a.supports_quality() and self.b.supports_quality()


class AdditiveBiMatcher(BiMatcher):
    """Base class for binary matchers where the scores of the sub-matchers are
    added together.
    """
    
    def quality(self):
        q = 0.0
        if self.a.is_active(): q += self.a.quality()
        if self.b.is_active(): q += self.b.quality()
        return q
    
    def block_quality(self):
        bq = 0.0
        if self.a.is_active(): bq += self.a.block_quality()
        if self.b.is_active(): bq += self.b.block_quality()
        return bq
    
    def weight(self):
        return (self.a.weight() + self.b.weight())
    
    def score(self):
        return (self.a.score() + self.b.score())
    

class UnionMatcher(AdditiveBiMatcher):
    """Matches the union (OR) of the postings in the two sub-matchers.
    """
    
    def replace(self):
        a = self.a.replace()
        b = self.b.replace()
        
        a_active = a.is_active()
        b_active = b.is_active()
        if not (a_active or b_active): return NullMatcher()
        if not a_active:
            return b
        if not b_active:
            return a
        
        if a is not self.a or b is not self.b:
            return self.__class__(a, b)
        return self
    
    def is_active(self):
        return self.a.is_active() or self.b.is_active()
    
    def skip_to(self, id):
        ra = rb = False
        if self.a.is_active():
            ra = self.a.skip_to(id)
        if self.b.is_active():
            rb = self.b.skip_to(id)
        return ra or rb
    
    def id(self):
        a = self.a
        b = self.b
        if not a.is_active(): return b.id()
        if not b.is_active(): return a.id()
        return min(a.id(), b.id())
    
    def all_ids(self):
        return iter(sorted(set(self.a.all_ids()) | set(self.b.all_ids())))
    
    def next(self):
        a = self.a
        b = self.b
        a_active = a.is_active()
        b_active = b.is_active()
        
        # Shortcut when one matcher is inactive
        if not (a_active or b_active):
            raise ReadTooFar
        elif not a_active:
            return b.next()
        elif not b_active:
            return a.next()
        
        a_id = a.id()
        b_id = b.id()
        ar = br = None
        if a_id <= b_id: ar = a.next()
        if b_id <= a_id: br = b.next()
        return ar or br
    
    def positions(self):
        if not self.a.is_active(): return self.b.positions()
        if not self.b.is_active(): return self.a.positions()
        
        id_a = self.a.id()
        id_b = self.b.id()
        if id_a < id_b:
            return self.a.positions()
        elif id_b < id_a:
            return self.b.positions()
        else:
            return sorted(set(self.a.positions())  | set(self.b.positions()))
    
    def score(self):
        a = self.a
        b = self.b
        
        if not a.is_active(): return b.score()
        if not b.is_active(): return a.score()
        
        id_a = a.id()
        id_b = b.id()
        if id_a < id_b:
            return a.score()
        elif id_b < id_a:
            return b.score()
        else:
            return (a.score() + b.score())
    
    def skip_to_quality(self, minquality):
        a = self.a
        b = self.b
        minquality = minquality
        
        # Short circuit if one matcher is inactive
        if not a.is_active():
            sk = b.skip_to_quality(minquality)
            return sk
        elif not b.is_active():
            return a.skip_to_quality(minquality)
        
        skipped = 0
        aq = a.block_quality()
        bq = b.block_quality()
        while a.is_active() and b.is_active() and aq + bq <= minquality:
            if aq < bq:
                skipped += a.skip_to_quality(minquality - bq)
                aq = a.block_quality()
            else:
                skipped += b.skip_to_quality(minquality - aq)
                bq = b.block_quality()
                
        return skipped
        

class DisjunctionMaxMatcher(UnionMatcher):
    """Matches the union (OR) of two sub-matchers. Where both sub-matchers
    match the same posting, returns the weight/score of the higher-scoring
    posting.
    """
    
    # TODO: this class inherits from AdditiveBiMatcher (through UnionMatcher)
    # but it does not add the scores of the sub-matchers together (it
    # overrides all methods that perform addition). Need to clean up the
    # inheritance. 
    
    def __init__(self, a, b, tiebreak=0.0):
        super(DisjunctionMaxMatcher, self).__init__(a, b)
        self.tiebreak = tiebreak
    
    def copy(self):
        return self.__class__(self.a.copy(), self.b.copy(),
                              tiebreak=self.tiebreak)
    
    def score(self):
        return max(self.a.score(), self.b.score())
    
    def quality(self):
        return max(self.a.quality(), self.b.quality())
    
    def block_quality(self):
        return max(self.a.block_quality(), self.b.block_quality())
    
    def skip_to_quality(self, minquality):
        a = self.a
        b = self.b
        minquality = minquality
        
        # Short circuit if one matcher is inactive
        if not a.is_active():
            sk = b.skip_to_quality(minquality)
            return sk
        elif not b.is_active():
            return a.skip_to_quality(minquality)
        
        skipped = 0
        aq = a.block_quality()
        bq = b.block_quality()
        while a.is_active() and b.is_active() and max(aq, bq) <= minquality:
            if aq <= minquality:
                skipped += a.skip_to_quality(minquality)
                aq = a.block_quality()
            if bq <= minquality:
                skipped += b.skip_to_quality(minquality)
                bq = b.block_quality()
        return skipped
        

class IntersectionMatcher(AdditiveBiMatcher):
    """Matches the intersection (AND) of the postings in the two sub-matchers.
    """
    
    def __init__(self, a, b):
        super(IntersectionMatcher, self).__init__(a, b)
        if (self.a.is_active()
            and self.b.is_active()
            and self.a.id() != self.b.id()):
            self._find_next()
    
    def replace(self):
        a = self.a.replace()
        b = self.b.replace()
        
        a_active = a
        b_active = b.is_active()
        if not (a_active and b_active): return NullMatcher()
        
        if a is not self.a or b is not self.b:
            return self.__class__(a, b)
        return self
    
    def is_active(self):
        return self.a.is_active() and self.b.is_active()
    
    def _find_next(self):
        a = self.a
        b = self.b
        a_id = a.id()
        b_id = b.id()
        assert a_id != b_id
        r = False
        
        while a.is_active() and b.is_active() and a_id != b_id:
            if a_id < b_id:
                ra = a.skip_to(b_id)
                if not a.is_active(): return
                r = r or ra
                a_id = a.id()
            else:
                rb = b.skip_to(a_id)
                if not b.is_active(): return
                r = r or rb
                b_id = b.id()
        return r
    
    def id(self):
        return self.a.id()
    
    def all_ids(self):
        return iter(sorted(set(self.a.all_ids()) & set(self.b.all_ids())))
    
    def skip_to(self, id):
        if not self.is_active(): raise ReadTooFar
        ra = self.a.skip_to(id)
        rb = self.b.skip_to(id)
        if self.is_active():
            rn = False
            if self.a.id() != self.b.id():
                rn = self._find_next()
            return ra or rb or rn
    
    def skip_to_quality(self, minquality):
        a = self.a
        b = self.b
        minquality = minquality
        
        skipped = 0
        aq = a.block_quality()
        bq = b.block_quality()
        while a.is_active() and b.is_active() and aq + bq <= minquality:
            if aq < bq:
                skipped += a.skip_to_quality(minquality - bq)
            else:
                skipped += b.skip_to_quality(minquality - aq)
            if a.id() != b.id():
                self._find_next()
            aq = a.block_quality()
            bq = b.block_quality()
        return skipped
    
    def next(self):
        if not self.is_active(): raise ReadTooFar
        
        # We must assume that the ids are equal whenever next() is called (they
        # should have been made equal by _find_next), so advance them both
        ar = self.a.next()
        if self.is_active():
            nr = self._find_next()
            return ar or nr
        
    def positions(self):
        return sorted(set(self.a.positions())  | set(self.b.positions()))


class AndNotMatcher(BiMatcher):
    """Matches the postings in the first sub-matcher that are NOT present in
    the second sub-matcher.
    """

    def __init__(self, a, b):
        super(AndNotMatcher, self).__init__(a, b)
        if (self.a.is_active()
            and self.b.is_active()
            and self.a.id() != self.b.id()):
            self._find_next()

    def is_active(self):
        return self.a.is_active()

    def _find_next(self):
        pos = self.a
        neg = self.b
        if not neg.is_active(): return
        pos_id = pos.id()
        r = False
        
        if neg.id() < pos_id:
            neg.skip_to(pos_id)
        
        while neg.is_active() and pos_id == neg.id():
            nr = pos.next()
            r = r or nr
            pos_id = pos.id()
            neg.skip_to(pos_id)
        
        return r
    
    def replace(self):
        if not self.a.is_active(): return NullMatcher()
        if not self.b.is_active(): return self.a
        return self
    
    def quality(self):
        return self.a.quality()
    
    def block_quality(self):
        return self.a.block_quality()
    
    def skip_to_quality(self, minquality):
        skipped = self.a.skip_to_quality(minquality)
        self._find_next()
        return skipped
    
    def id(self):
        return self.a.id()
    
    def all_ids(self):
        return iter(sorted(set(self.a.all_ids()) - set(self.b.all_ids())))
    
    def next(self):
        if not self.a.is_active(): raise ReadTooFar
        ar = self.a.next()
        nr = False
        if self.b.is_active():
            nr = self._find_next()
        return ar or nr
        
    def skip_to(self, id):
        if not self.a.is_active(): raise ReadTooFar
        if id < self.a.id(): return
        
        self.a.skip_to(id)
        if self.b.is_active():
            self.b.skip_to(id)
            self._find_next()
    
    def weight(self):
        return self.a.weight()
    
    def score(self):
        return self.a.score()


class InverseMatcher(WrappingMatcher):
    """Synthetic matcher, generates postings that are NOT present in the
    wrapped matcher.
    """
    
    def __init__(self, child, limit, missing=None, weight=1.0):
        super(InverseMatcher, self).__init__(child)
        self.limit = limit
        self._weight = weight
        self.missing = missing or (lambda id: False)
        self._id = 0
        self._find_next()
    
    def copy(self):
        return self.__class__(self.child.copy(), self.limit,
                              weight=self._weight, missing=self.missing)
    
    def is_active(self):
        return self._id < self.limit
    
    def supports_quality(self):
        return False
    
    def _find_next(self):
        child = self.child
        missing = self.missing
        
        if not child.is_active() and not missing(self._id):
            return
        
        if child.is_active() and child.id() < self._id:
            child.skip_to(self._id)
        
        while self._id < self.limit and ((child.is_active() and self._id == child.id()) or missing(id)):
            self._id += 1
            if child.is_active():
                child.next()
    
    def id(self):
        return self._id
    
    def all_ids(self):
        missing = self.missing
        negs = set(self.child.all_ids())
        return (id for id in xrange(self.limit)
                if id not in negs and not missing(id))
    
    def next(self):
        if self._id >= self.limit: raise ReadTooFar
        self._id += 1
        self._find_next()
        
    def skip_to(self, id):
        if self._id >= self.limit: raise ReadTooFar
        if id < self._id: return
        self._id = id
        self._find_next()
    
    def weight(self):
        return self._weight
    
    def score(self):
        return self._weight


class RequireMatcher(WrappingMatcher):
    """Matches postings that are in both sub-matchers, but only uses scores
    from the first.
    """
    
    def __init__(self, a, b):
        self.a = a
        self.b = b
        self.child = IntersectionMatcher(a, b)
    
    def copy(self):
        return self.__class__(self.a.copy(), self.b.copy())
    
    def replace(self):
        if not self.child.is_active(): return NullMatcher()
        return self
    
    def quality(self):
        return self.a.quality()
    
    def block_quality(self):
        return self.a.block_quality()
    
    def skip_to_quality(self, minquality):
        skipped = self.a.skip_to_quality(minquality)
        self.child._find_next()
        return skipped
    
    def weight(self):
        return self.a.weight()
    
    def score(self):
        return self.a.score()


class AndMaybeMatcher(AdditiveBiMatcher):
    """Matches postings in the first sub-matcher, and if the same posting is
    in the second sub-matcher, adds their scores.
    """
    
    def __init__(self, a, b):
        self.a = a
        self.b = b
        
        if a.is_active() and b.is_active() and a.id() != b.id():
            b.skip_to(a.id())
    
    def is_active(self):
        return self.a.is_active()
    
    def id(self):
        return self.a.id()
    
    def next(self):
        if not self.a.is_active(): raise ReadTooFar
        
        ar = self.a.next()
        br = False
        if self.a.is_active() and self.b.is_active():
            br = self.b.skip_to(self.a.id())
        return ar or br
    
    def skip_to(self, id):
        if not self.a.is_active(): raise ReadTooFar
        
        ra = self.a.skip_to(id)
        rb = False
        if self.a.is_active() and self.b.is_active():
            rb = self.b.skip_to(id)
        return ra or rb
    
    def replace(self):
        ar = self.a.replace()
        br = self.b.replace()
        if not ar.is_active(): return NullMatcher()
        if not br.is_active(): return ar
        if ar is not self.a or br is not self.b:
            return self.__class__(ar, br)
        return self
    
    def skip_to_quality(self, minquality):
        a = self.a
        b = self.b
        minquality = minquality
        
        if not a.is_active(): raise ReadTooFar
        if not b.is_active():
            return a.skip_to_quality(minquality)
        
        skipped = 0
        aq = a.block_quality()
        bq = b.block_quality()
        while a.is_active() and b.is_active() and aq + bq <= minquality:
            if aq < bq:
                skipped += a.skip_to_quality(minquality - bq)
                aq = a.block_quality()
            else:
                skipped += b.skip_to_quality(minquality - aq)
                bq = b.block_quality()
        
        return skipped
    
    def weight(self):
        if self.a.id() == self.b.id():
            return self.a.weight() + self.b.weight()
        else:
            return self.a.weight()
    
    def score(self):
        if self.b.is_active() and self.a.id() == self.b.id():
            return self.a.score() + self.b.score()
        else:
            return self.a.score()
    

class PhraseMatcher(WrappingMatcher):
    """Base class for phrase matchers.
    """
    
    def __init__(self, wordmatchers, slop=1, boost=1.0):
        self.wordmatchers = wordmatchers
        self.child = make_binary_tree(IntersectionMatcher, wordmatchers)
        self.slop = slop
        self.boost = boost
        self._find_next()
    
    def replace(self):
        if not self.is_active():
            return NullMatcher()
        return self
    
    def next(self):
        ri = self.child.next()
        rn = self._find_next()
        return ri or rn
    
    def skip_to(self, id):
        rs = self.child.skip_to(id)
        rn = self._find_next()
        return rs or rn
    
    def _find_next(self):
        isect = self.child
        slop = self.slop
        
        # List of "active" positions
        current = []
        
        while not current and isect.is_active():
            # [[list of positions for word 1],
            #  [list of positions for word 2], ...]
            poses = [m.positions() for m in self.wordmatchers]
            
            # Set the "active" position list to the list of positions of the
            # first word. We well then iteratively update this list with the
            # positions of subsequent words if they are within the "slop"
            # distance of the positions in the active list.
            current = poses[0]
            
            # For each list of positions for the subsequent words...
            for poslist in poses[1:]:
                # A list to hold the new list of active positions
                newposes = []
                
                # For each position in the list of positions in this next word
                for newpos in poslist:
                    # Use bisect to only check the part of the current list
                    # that could contain positions within the "slop" distance
                    # of the new position
                    start = bisect_left(current, newpos - slop)
                    end = bisect_right(current, newpos)
                    
                    # 
                    for curpos in current[start:end]:
                        delta = newpos - curpos
                        if delta > 0 and delta <= slop:
                            newposes.append(newpos)
                    
                current = newposes
                if not current: break
            
            if not current:
                isect.next()
        
        self._count = len(current)


#class VectorPhraseMatcher(BasePhraseMatcher):
#    """Phrase matcher for fields with a vector with positions (i.e. Positions
#    or CharacterPositions format).
#    """
#    
#    def __init__(self, searcher, fieldname, words, isect, slop=1, boost=1.0):
#        """
#        :param searcher: a Searcher object.
#        :param fieldname: the field in which to search.
#        :param words: a sequence of token texts representing the words in the
#            phrase.
#        :param isect: an intersection matcher for the words in the phrase.
#        :param slop: 
#        """
#        
#        decodefn = searcher.field(fieldname).vector.decoder("positions")
#        self.reader = searcher.reader()
#        self.fieldname = fieldname
#        self.words = words
#        self.sortedwords = sorted(self.words)
#        super(VectorPhraseMatcher, self).__init__(isect, decodefn, slop=slop,
#                                                  boost=boost)
#    
#    def _poses(self):
#        vreader = self.reader.vector(self.child.id(), self.fieldname)
#        poses = {}
#        decode_positions = self.decode_positions
#        for word in self.sortedwords:
#            vreader.skip_to(word)
#            if vreader.id() != word:
#                raise Exception("Phrase query: %r in term index but not in"
#                                " vector (possible analyzer mismatch)" % word)
#            poses[word] = decode_positions(vreader.value())
#        # Now put the position lists in phrase order
#        return [poses[word] for word in self.words]












