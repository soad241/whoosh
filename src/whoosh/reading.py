#===============================================================================
# Copyright 2007 Matt Chaput
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

"""This module contains classes that allow reading from an index.
"""

from bisect import bisect_right
from heapq import heapify, heapreplace, heappop, nlargest

from whoosh.fields import UnknownFieldError
from whoosh.util import ClosableMixin
from whoosh.matching import MultiMatcher

# Exceptions

class TermNotFound(Exception):
    pass


# Base class

class IndexReader(ClosableMixin):
    """Do not instantiate this object directly. Instead use Index.reader().
    """

    def __contains__(self, term):
        """Returns True if the given term tuple (fieldname, text) is
        in this reader.
        """
        raise NotImplementedError

    def __iter__(self):
        """Yields (fieldname, text, docfreq, indexfreq) tuples for each term in
        the reader, in lexical order.
        """
        raise NotImplementedError

    def close(self):
        """Closes the open files associated with this reader.
        """
        raise NotImplementedError

    def generation(self):
        """Returns the generation of the index being read, or -1 if the backend
        is not versioned.
        """
        
        return -1

    def iter_from(self, fieldname, text):
        """Yields (field_num, text, doc_freq, index_freq) tuples for all terms
        in the reader, starting at the given term.
        """
        raise NotImplementedError

    def expand_prefix(self, fieldname, prefix):
        """Yields terms in the given field that start with the given prefix.
        """

        for fn, t, _, _ in self.iter_from(fieldname, prefix):
            if fn != fieldname or not t.startswith(prefix):
                return
            yield t

    def all_terms(self):
        """Yields (fieldname, text) tuples for every term in the index.
        """

        for fn, t, _, _ in self:
            yield (fn, t)

    def iter_field(self, fieldname, prefix=''):
        """Yields (text, doc_freq, index_freq) tuples for all terms in the
        given field.
        """

        for fn, t, docfreq, freq in self.iter_from(fieldname, prefix):
            if fn != fieldname:
                return
            yield t, docfreq, freq

    def iter_prefix(self, fieldname, prefix):
        """Yields (field_num, text, doc_freq, index_freq) tuples for all terms
        in the given field with a certain prefix.
        """

        for fn, t, docfreq, colfreq in self.iter_from(fieldname, prefix):
            if fn != fieldname or not t.startswith(prefix):
                return
            yield (t, docfreq, colfreq)

    def lexicon(self, fieldname):
        """Yields all terms in the given field.
        """

        for t, _, _ in self.iter_field(fieldname):
            yield t

    def has_deletions(self):
        """Returns True if the underlying index/segment has deleted
        documents.
        """
        raise NotImplementedError

    def is_deleted(self, docnum):
        """Returns True if the given document number is marked deleted.
        """
        raise NotImplementedError

    def stored_fields(self, docnum):
        """Returns the stored fields for the given document number.
        
        :param numerickeys: use field numbers as the dictionary keys instead of
            field names.
        """
        raise NotImplementedError

    def all_stored_fields(self):
        """Yields the stored fields for all documents.
        """
        
        for docnum in xrange(self.doc_count_all()):
            if not self.is_deleted(docnum):
                yield self.stored_fields(docnum)

    def doc_count_all(self):
        """Returns the total number of documents, DELETED OR UNDELETED,
        in this reader.
        """
        raise NotImplementedError

    def doc_count(self):
        """Returns the total number of UNDELETED documents in this reader.
        """
        raise NotImplementedError

    def doc_frequency(self, fieldname, text):
        """Returns how many documents the given term appears in.
        """
        raise NotImplementedError

    def frequency(self, fieldname, text):
        """Returns the total number of instances of the given term in the
        collection.
        """
        raise NotImplementedError

    def field(self, fieldname):
        """Returns the Field object corresponding to the given field name.
        """
        
        return self.schema[fieldname]

    def field_names(self):
        """Returns a list of the field names in the index's schema.
        """
        
        return self.schema.names()

    def scorable(self, fieldname):
        """Returns true if the given field stores field lengths.
        """
        
        return self.field(fieldname).scorable

    def format(self, fieldname):
        """Returns the Format object corresponding to the given field name.
        """
        
        return self.field(fieldname).format

    def scorable_names(self):
        """Returns a list of the names of fields that store field lengths.
        """
        
        return self.schema.scorable_names()

    def vector_names(self):
        """Returns a list of the names of fields that store vectors.
        """
        
        return self.schema.vector_names()

    def vector_format(self, fieldname):
        """Returns the Format object corresponding to the given field's vector.
        """
        raise NotImplementedError

    def field_length(self, fieldname):
        """Returns the total number of terms in the given field. This is used
        by some scoring algorithms.
        """
        raise NotImplementedError

    def doc_field_length(self, docnum, fieldname, default=0):
        """Returns the number of terms in the given field in the given
        document. This is used by some scoring algorithms.
        """
        raise NotImplementedError

    def doc_field_lengths(self, docnum):
        """Returns an iterator of (fieldname, length) pairs for the given
        document. This is used internally.
        """
        
        for fieldname in self.scorable_names():
            length = self.doc_field_length(docnum, fieldname)
            if length:
                yield (fieldname, length)
    
    def max_field_length(self, fieldname, default=0):
        """Returns the maximum length of the field across all documents.
        """
        raise NotImplementedError

    def postings(self, fieldname, text, exclude_docs=None):
        """Returns a :class:`~whoosh.matching.Matcher` for the postings of the
        given term.
        
        >>> pr = searcher.postings("content", "render")
        >>> pr.skip_to(10)
        >>> pr.id
        12
        
        :param fieldname: the field name or field number of the term.
        :param text: the text of the term.
        :exclude_docs: an optional BitVector of documents to exclude from the
            results, or None to not exclude any documents.
        :rtype: :class:`whoosh.matching.Matcher`
        """

        raise NotImplementedError

    def has_vector(self, docnum, fieldname):
        """Returns True if the given document has a term vector for the given
        field.
        """
        raise NotImplementedError

    def vector(self, docnum, fieldname):
        """Returns a :class:`~whoosh.matching.Matcher` object for the
        given term vector.
        
        >>> docnum = searcher.document_number(path=u'/a/b/c')
        >>> v = searcher.vector(docnum, "content")
        >>> v.all_as("frequency")
        [(u"apple", 3), (u"bear", 2), (u"cab", 2)]
        
        :param docnum: the document number of the document for which you want
            the term vector.
        :param fieldname: the field name or field number of the field for which
            you want the term vector.
        :rtype: :class:`whoosh.matching.Matcher`
        """
        raise NotImplementedError

    def vector_as(self, astype, docnum, fieldname):
        """Returns an iterator of (termtext, value) pairs for the terms in the
        given term vector. This is a convenient shortcut to calling vector()
        and using the Matcher object when all you want are the terms and/or
        values.
        
        >>> docnum = searcher.document_number(path=u'/a/b/c')
        >>> searcher.vector_as("frequency", docnum, "content")
        [(u"apple", 3), (u"bear", 2), (u"cab", 2)]
        
        :param docnum: the document number of the document for which you want
            the term vector.
        :param fieldname: the field name or field number of the field for which
            you want the term vector.
        :param astype: a string containing the name of the format you want the
            term vector's data in, for example "weights".
        """

        vec = self.vector(docnum, fieldname)
        if astype == "weight":
            while vec.is_active():
                yield (vec.id(), vec.weight())
                vec.next()
        else:
            format = self.format(fieldname)
            decoder = format.decoder(astype)
            while vec.is_active():
                yield (vec.id(), decoder(vec.value()))
                vec.next()

    def most_frequent_terms(self, fieldname, number=5, prefix=''):
        """Returns the top 'number' most frequent terms in the given field as a
        list of (frequency, text) tuples.
        """

        return nlargest(number, ((tf, token)
                                 for token, _, tf
                                 in self.iter_prefix(fieldname, prefix)))

    def most_distinctive_terms(self, fieldname, number=5, prefix=None):
        """Returns the top 'number' terms with the highest ``tf*idf`` scores as
        a list of (score, text) tuples.
        """

        return nlargest(number, ((tf * (1.0 / df), token)
                                 for token, df, tf
                                 in self.iter_prefix(fieldname, prefix)))


# Multisegment reader class

class MultiReader(IndexReader):
    """Do not instantiate this object directly. Instead use Index.reader().
    """

    def __init__(self, readers, generation=-1):
        self.readers = readers
        self._generation = generation
        
        self.doc_offsets = []
        base = 0
        for r in self.readers:
            self.doc_offsets.append(base)
            base += r.doc_count_all()
        
        self.is_closed = False

    def __contains__(self, term):
        return any(r.__contains__(term) for r in self.readers)

    def __iter__(self):
        return self._merge_iters([iter(r) for r in self.readers])

    def _document_segment(self, docnum):
        return max(0, bisect_right(self.doc_offsets, docnum) - 1)

    def _segment_and_docnum(self, docnum):
        segmentnum = self._document_segment(docnum)
        offset = self.doc_offsets[segmentnum]
        return segmentnum, docnum - offset

    def _merge_iters(self, iterlist):
        # Merge-sorts terms coming from a list of
        # term iterators (IndexReader.__iter__() or
        # IndexReader.iter_from()).

        # Fill in the list with the head term from each iterator.

        current = []
        for it in iterlist:
            fnum, text, docfreq, termcount = it.next()
            current.append((fnum, text, docfreq, termcount, it))
        heapify(current)

        # Number of active iterators
        active = len(current)
        while active > 0:
            # Peek at the first term in the sorted list
            fnum, text = current[0][:2]
            docfreq = 0
            termcount = 0

            # Add together all terms matching the first term in the list.
            while current and current[0][0] == fnum and current[0][1] == text:
                docfreq += current[0][2]
                termcount += current[0][3]
                it = current[0][4]
                try:
                    fn, t, df, tc = it.next()
                    heapreplace(current, (fn, t, df, tc, it))
                except StopIteration:
                    heappop(current)
                    active -= 1

            # Yield the term with the summed doc frequency and term count.
            yield (fnum, text, docfreq, termcount)

    def close(self):
        for d in self.readers:
            d.close()
        self.is_closed = True

    def generation(self):
        return self._generation

    def iter_from(self, fieldname, text):
        return self._merge_iters([r.iter_from(fieldname, text)
                                  for r in self.readers])

    # expand_prefix
    # all_terms
    # iter_field
    # iter_prefix
    # lexicon

    def has_deletions(self):
        return any(r.has_deletions() for r in self.readers)

    def is_deleted(self, docnum):
        segmentnum, segmentdoc = self._segment_and_docnum(docnum)
        return self.readers[segmentnum].is_deleted(segmentdoc)

    def stored_fields(self, docnum):
        segmentnum, segmentdoc = self._segment_and_docnum(docnum)
        return self.readers[segmentnum].stored_fields(segmentdoc)

    def all_stored_fields(self):
        for reader in self.readers:
            for result in reader.all_stored_fields():
                yield result

    def doc_count_all(self):
        return sum(dr.doc_count_all() for dr in self.readers)

    def doc_count(self):
        return sum(dr.doc_count() for dr in self.readers)

    def field(self, fieldname):
        for r in self.readers:
            try:
                field = r.field(fieldname)
                return field
            except KeyError:
                pass
        raise KeyError("No field named %r" % fieldname)

    def field_names(self):
        s = set()
        for reader in self.readers:
            s = s.union(reader.field_names())
        return sorted(s)

    def scorable(self, fieldname):
        return any(r.scorable(fieldname) for r in self.readers)

    def scorable_names(self):
        s = set()
        for reader in self.readers:
            s = s.union(reader.scorable_names())
        return sorted(s)

    def vector_names(self):
        s = set()
        for reader in self.readers:
            s = s.union(reader.vector_names())
        return sorted(s)

    def field_length(self, fieldname):
        return sum(dr.field_length(fieldname) for dr in self.readers)

    def doc_field_length(self, docnum, fieldname, default=0):
        segmentnum, segmentdoc = self._segment_and_docnum(docnum)
        reader = self.readers[segmentnum]
        return reader.doc_field_length(segmentdoc, fieldname, default=default)
    
    # max_field_length

    def postings(self, fieldname, text, scorefns=None, exclude_docs=None):
        postreaders = []
        docoffsets = []
        for i, r in enumerate(self.readers):
            format = r.field(fieldname).format
            if (fieldname, text) in r:
                pr = r.postings(fieldname, text, scorefns=scorefns,
                                exclude_docs=exclude_docs)
                postreaders.append(pr)
                docoffsets.append(self.doc_offsets[i])
        
        if not postreaders:
            raise TermNotFound(fieldname, text)
        else:
            return MultiMatcher(postreaders, docoffsets)

    def has_vector(self, docnum, fieldname):
        segmentnum, segmentdoc = self._segment_and_docnum(docnum)
        return self.readers[segmentnum].has_vector(segmentdoc, fieldname)

    def vector(self, docnum, fieldname):
        segmentnum, segmentdoc = self._segment_and_docnum(docnum)
        return self.readers[segmentnum].vector(segmentdoc, fieldname)

    def vector_as(self, astype, docnum, fieldname):
        segmentnum, segmentdoc = self._segment_and_docnum(docnum)
        return self.readers[segmentnum].vector_as(astype, segmentdoc, fieldname)

    def format(self, fieldname):
        for r in self.readers:
            fmt = r.format(fieldname)
            if fmt is not None:
                return fmt

    def vector_format(self, fieldname):
        for r in self.readers:
            vfmt = r.vector_format(fieldname)
            if vfmt is not None:
                return vfmt

    def doc_frequency(self, fieldname, text):
        return sum(r.doc_frequency(fieldname, text) for r in self.readers)

    def frequency(self, fieldname, text):
        return sum(r.frequency(fieldname, text) for r in self.readers)

    # most_frequent_terms
    # most_distinctive_terms




















