#===============================================================================
# Copyright 2009 Matt Chaput
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

import cPickle, os, re
from bisect import bisect_right
from time import time
from threading import Lock

from whoosh import __version__
from whoosh.fields import Schema
from whoosh.index import Index
from whoosh.index import EmptyIndexError, IndexVersionError
from whoosh.index import _DEF_INDEX_NAME
from whoosh.store import Storage, LockError
from whoosh.system import _INT_SIZE, _FLOAT_SIZE, _LONG_SIZE


_INDEX_VERSION = -108


# TOC read/write functions

def _toc_filename(indexname, gen):
    return "_%s_%s.toc" % (indexname, gen)

def _toc_pattern(indexname):
    """Returns a regular expression object that matches TOC filenames.
    name is the name of the index.
    """

    return re.compile("^_%s_([0-9]+).toc$" % indexname)

def _segment_pattern(indexname):
    """Returns a regular expression object that matches segment filenames.
    name is the name of the index.
    """

    return re.compile("(_%s_[0-9]+).(%s)" % (indexname,
                                             Segment.EXTENSIONS.values()))


def _latest_generation(storage, indexname):
    pattern = _toc_pattern(indexname)

    max = -1
    for filename in storage:
        m = pattern.match(filename)
        if m:
            num = int(m.group(1))
            if num > max: max = num
    return max


def _create_index(storage, schema, indexname=_DEF_INDEX_NAME):
    # Clear existing files
    prefix = "_%s_" % indexname
    for filename in storage:
        if filename.startswith(prefix):
            storage.delete_file(filename)
    
    _write_toc(storage, schema, indexname, 0, 0, SegmentSet())


def _write_toc(storage, schema, indexname, gen, segment_counter, segments):
    schema.clean()

    # Use a temporary file for atomic write.
    tocfilename = _toc_filename(indexname, gen)
    tempfilename = '%s.%s' % (tocfilename, time())
    stream = storage.create_file(tempfilename)

    stream.write_varint(_INT_SIZE)
    stream.write_varint(_LONG_SIZE)
    stream.write_varint(_FLOAT_SIZE)
    stream.write_int(-12345)

    stream.write_int(_INDEX_VERSION)
    for num in __version__[:3]:
        stream.write_varint(num)

    stream.write_string(cPickle.dumps(schema, -1))
    stream.write_int(gen)
    stream.write_int(segment_counter)
    stream.write_pickle(segments)
    stream.close()

    # Rename temporary file to the proper filename
    storage.rename_file(tempfilename, tocfilename, safe=True)


class Toc(object):
    def __init__(self, **kwargs):
        for name, value in kwargs.iteritems():
            setattr(self, name, value)
        

def _read_toc(storage, schema, indexname):
    gen = _latest_generation(storage, indexname)
    if gen < 0:
        raise EmptyIndexError("Index %r does not exist in %r" % (indexname, storage))
    
    # Read the content of this index from the .toc file.
    tocfilename = _toc_filename(indexname, gen)
    stream = storage.open_file(tocfilename)

    def check_size(name, target):
        sz = stream.read_varint()
        if sz != target:
            raise IndexError("Index was created on different architecture: saved %s = %s, this computer = %s" % (name, sz, target))

    check_size("int", _INT_SIZE)
    check_size("long", _LONG_SIZE)
    check_size("float", _FLOAT_SIZE)

    if not stream.read_int() == -12345:
        raise IndexError("Number misread: byte order problem")

    version = stream.read_int()
    if version != _INDEX_VERSION:
        raise IndexVersionError("Can't read format %s" % version, version)
    release = (stream.read_varint(), stream.read_varint(), stream.read_varint())
    
    # If the user supplied a schema object with the constructor, don't load
    # the pickled schema from the saved index.
    if schema:
        stream.skip_string()
    else:
        schema = cPickle.loads(stream.read_string())
    
    # Generation
    assert gen == stream.read_int()
    
    segment_counter = stream.read_int()
    segments = stream.read_pickle()
    
    stream.close()
    return Toc(version=version, release=release, schema=schema,
               segment_counter=segment_counter, segments=segments,
               generation=gen)


def _next_segment_name(self):
        #Returns the name of the next segment in sequence.
        if self.segment_num_lock is None:
            self.segment_num_lock = Lock()
        
        if self.segment_num_lock.acquire():
            try:
                self.segment_counter += 1
                return 
            finally:
                self.segment_num_lock.release()
        else:
            raise LockError


def _clean_files(storage, indexname, gen, segments):
        # Attempts to remove unused index files (called when a new generation
        # is created). If existing Index and/or reader objects have the files
        # open, they may not be deleted immediately (i.e. on Windows) but will
        # probably be deleted eventually by a later call to clean_files.

        current_segment_names = set(s.name for s in segments)

        tocpattern = _toc_pattern(indexname)
        segpattern = _segment_pattern(indexname)

        todelete = set()
        for filename in storage:
            tocm = tocpattern.match(filename)
            segm = segpattern.match(filename)
            if tocm:
                if int(tocm.group(1)) != gen:
                    todelete.add(filename)
            elif segm:
                name = segm.group(1)
                if name not in current_segment_names:
                    todelete.add(filename)
        
        for filename in todelete:
            try:
                storage.delete_file(filename)
            except OSError:
                # Another process still has this file open
                pass


# Index placeholder object

class FileIndex(Index):
    def __init__(self, storage, schema=None, indexname=_DEF_INDEX_NAME):
        if not isinstance(storage, Storage):
            raise ValueError("%r is not a Storage object" % storage)
        if schema is not None and not isinstance(schema, Schema):
            raise ValueError("%r is not a Schema object" % schema)
        if not isinstance(indexname, (str, unicode)):
            raise ValueError("indexname %r is not a string" % indexname)
        
        self.storage = storage
        self._schema = schema
        self.indexname = indexname
        
        # Try reading the TOC to see if it's possible
        _read_toc(self.storage, self._schema, self.indexname)

    def __repr__(self):
        return "%s(%r, %r)" % (self.__class__.__name__,
                               self.storage, self.indexname)

    def close(self):
        pass

    # add_field
    # remove_field
    
    def latest_generation(self):
        return _latest_generation(self.storage, self.indexname)
    
    # refresh
    # up_to_date
    
    def last_modified(self):
        gen = self.latest_generation()
        filename = _toc_filename(self.indexname, gen)
        return self.storage.file_modified(filename)

    def is_empty(self):
        info = _read_toc(self.storage, self.schema, self.indexname)
        return len(info.segments) == 0
    
    def optimize(self):
        w = self.writer()
        w.commit(optimize=True)

    def doc_count_all(self):
        return self._segments().doc_count_all()

    def doc_count(self):
        return self._segments().doc_count()

    def field_length(self, fieldname):
        return self._segments().field_length(fieldname)

    # searcher
    
    def reader(self):
        lock = self.lock("READLOCK")
        lock.acquire(True)
        try:
            info = self._read_toc()
            return info.segments.reader(self.storage, info.schema, info.generation)
        finally:
            lock.release()

    def writer(self, **kwargs):
        from whoosh.filedb.filewriting import SegmentWriter
        return SegmentWriter(self, **kwargs)

    def lock(self, name):
        """Returns a lock object that you can try to call acquire() on to
        lock the index.
        """
        
        return self.storage.lock(self.indexname + "_" + name)

    def _read_toc(self):
        return _read_toc(self.storage, self._schema, self.indexname)

    def _segments(self):
        return self._read_toc().segments
    
    def _current_schema(self):
        return self._read_toc().schema


# SegmentSet object

class SegmentSet(object):
    """This class is never instantiated by the user. It is used by the Index
    object to keep track of the segments in the index.
    """

    def __init__(self, segments=None):
        if segments is None:
            self.segments = []
        else:
            self.segments = segments

        self._doc_offsets = self.doc_offsets()

    def __repr__(self):
        return repr(self.segments)

    def __len__(self):
        """
        :returns: the number of segments in this set.
        """
        return len(self.segments)

    def __iter__(self):
        return iter(self.segments)

    def __getitem__(self, n):
        return self.segments.__getitem__(n)

    def append(self, segment):
        """Adds a segment to this set."""

        self.segments.append(segment)
        self._doc_offsets = self.doc_offsets()

    def _document_segment(self, docnum):
        """Returns the index.Segment object containing the given document
        number.
        """

        offsets = self._doc_offsets
        if len(offsets) == 1: return 0
        return bisect_right(offsets, docnum) - 1

    def _segment_and_docnum(self, docnum):
        """Returns an (index.Segment, segment_docnum) pair for the segment
        containing the given document number.
        """

        segmentnum = self._document_segment(docnum)
        offset = self._doc_offsets[segmentnum]
        segment = self.segments[segmentnum]
        return segment, docnum - offset

    def copy(self):
        """:returns: a deep copy of this set."""
        return self.__class__([s.copy() for s in self.segments])

    def filenames(self):
        nameset = set()
        for segment in self.segments:
            nameset |= segment.filenames()
        return nameset

    def doc_offsets(self):
        # Recomputes the document offset list. This must be called if you
        # change self.segments.
        offsets = []
        base = 0
        for s in self.segments:
            offsets.append(base)
            base += s.doc_count_all()
        return offsets

    def doc_count_all(self):
        """
        :returns: the total number of documents, DELETED or UNDELETED, in this
            set.
        """
        return sum(s.doc_count_all() for s in self.segments)

    def doc_count(self):
        """
        :returns: the number of undeleted documents in this set.
        """
        return sum(s.doc_count() for s in self.segments)

    def field_length(self, fieldname):
        return sum(s.field_length(fieldname) for s in self.segments)

    def has_deletions(self):
        """
        :returns: True if this index has documents that are marked deleted but
            haven't been optimized out of the index yet.
        """
        
        return any(s.has_deletions() for s in self.segments)

    def delete_document(self, docnum, delete=True):
        """Deletes a document by number.
        """

        segment, segdocnum = self._segment_and_docnum(docnum)
        segment.delete_document(segdocnum, delete=delete)

    def deleted_count(self):
        """
        :returns: the total number of deleted documents in this index.
        """
        return sum(s.deleted_count() for s in self.segments)

    def is_deleted(self, docnum):
        """
        :returns: True if a given document number is deleted but not yet
            optimized out of the index.
        """

        segment, segdocnum = self._segment_and_docnum(docnum)
        return segment.is_deleted(segdocnum)

    def reader(self, storage, schema, generation):
        from whoosh.filedb.filereading import SegmentReader
        
        segments = self.segments
        if len(segments) == 1:
            r = SegmentReader(storage, schema, segments[0], generation)
        else:
            from whoosh.reading import MultiReader
            readers = [SegmentReader(storage, schema, segment, -2)
                       for segment in segments]
            r = MultiReader(readers, generation)
            
        return r


class Segment(object):
    """Do not instantiate this object directly. It is used by the Index object
    to hold information about a segment. A list of objects of this class are
    pickled as part of the TOC file.
    
    The TOC file stores a minimal amount of information -- mostly a list of
    Segment objects. Segments are the real reverse indexes. Having multiple
    segments allows quick incremental indexing: just create a new segment for
    the new documents, and have the index overlay the new segment over previous
    ones for purposes of reading/search. "Optimizing" the index combines the
    contents of existing segments into one (removing any deleted documents
    along the way).
    """

    EXTENSIONS = {"fieldlengths": "fln", "storedfields": "sto",
                  "termsindex": "trm", "termposts": "pst",
                  "vectorindex": "vec", "vectorposts": "vps"}
    
    def __init__(self, name, doccount, fieldlength_totals, fieldlength_maxes,
                 deleted=None):
        """
        :param name: The name of the segment (the Index object computes this
            from its name and the generation).
        :param doccount: The maximum document number in the segment.
        :param term_count: Total count of all terms in all documents.
        :param fieldlength_totals: A dictionary mapping field numbers to the
            total number of terms in that field across all documents in the
            segment.
        :param deleted: A set of deleted document numbers, or None if no
            deleted documents exist in this segment.
        """

        assert isinstance(name, basestring)
        assert isinstance(doccount, (int, long))
        assert fieldlength_totals is None or isinstance(fieldlength_totals, dict), "fl_totals=%r" % fieldlength_totals
        assert fieldlength_maxes is None or isinstance(fieldlength_maxes, dict), "fl_maxes=%r" % fieldlength_maxes
        
        self.name = name
        self.doccount = doccount
        self.fieldlength_totals = fieldlength_totals
        self.fieldlength_maxes = fieldlength_maxes
        self.deleted = deleted
        
        self._filenames = set()
        for attr, ext in self.EXTENSIONS.iteritems():
            fname = "%s.%s" % (self.name, ext)
            setattr(self, attr + "_filename", fname)
            self._filenames.add(fname)

    def __repr__(self):
        return "%s(%r)" % (self.__class__.__name__, self.name)

    def copy(self):
        if self.deleted:
            deleted = set(self.deleted)
        else:
            deleted = None
        return Segment(self.name, self.doccount, self.fieldlength_totals,
                       self.fieldlength_maxes, deleted)

    def filenames(self):
        return self._filenames

    def doc_count_all(self):
        """
        :returns: the total number of documents, DELETED OR UNDELETED, in this
            segment.
        """
        return self.doccount

    def doc_count(self):
        """
        :returns: the number of (undeleted) documents in this segment.
        """
        return self.doccount - self.deleted_count()

    def has_deletions(self):
        """
        :returns: True if any documents in this segment are deleted.
        """
        return self.deleted_count() > 0

    def deleted_count(self):
        """
        :returns: the total number of deleted documents in this segment.
        """
        if self.deleted is None: return 0
        return len(self.deleted)

    def field_length(self, fieldname, default=0):
        """Returns the total number of terms in the given field across all
        documents in this segment.
        """
        return self.fieldlength_totals.get(fieldname, default)

    def max_field_length(self, fieldname, default=0):
        """Returns the maximum length of the given field in any of the
        documents in the segment.
        """
        return self.fieldlength_maxes.get(fieldname, default)

    def delete_document(self, docnum, delete=True):
        """Deletes the given document number. The document is not actually
        removed from the index until it is optimized.

        :param docnum: The document number to delete.
        :param delete: If False, this undeletes a deleted document.
        """

        if delete:
            if self.deleted is None:
                self.deleted = set()
            elif docnum in self.deleted:
                raise KeyError("Document %s in segment %r is already deleted"
                               % (docnum, self.name))

            self.deleted.add(docnum)
        else:
            if self.deleted is None or docnum not in self.deleted:
                raise KeyError("Document %s is not deleted" % docnum)

            self.deleted.clear(docnum)

    def is_deleted(self, docnum):
        """:returns: True if the given document number is deleted."""

        if self.deleted is None: return False
        return docnum in self.deleted


























