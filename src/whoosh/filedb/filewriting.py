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

from collections import defaultdict

from whoosh.fields import UnknownFieldError
from whoosh.filedb.fileindex import Segment, SegmentSet
from whoosh.filedb.filepostings import FilePostingWriter
from whoosh.filedb.filetables import (StoredFieldWriter, CodedOrderedWriter,
                                      CodedHashWriter)
from whoosh.filedb import misc
from whoosh.filedb.pools import TempfilePool
from whoosh.store import LockError
from whoosh.support.filelock import try_for
from whoosh.util import fib
from whoosh.writing import IndexWriter


# Merge policies

# A merge policy is a callable that takes the Index object, the SegmentWriter
# object, and the current SegmentSet (not including the segment being written),
# and returns an updated SegmentSet (not including the segment being written).

def NO_MERGE(writer, segments):
    """This policy does not merge any existing segments.
    """
    return segments


def MERGE_SMALL(writer, segments):
    """This policy merges small segments, where "small" is defined using a
    heuristic based on the fibonacci sequence.
    """

    from whoosh.filedb.filereading import SegmentReader
    newsegments = SegmentSet()
    sorted_segment_list = sorted((s.doc_count_all(), s) for s in segments)
    total_docs = 0
    for i, (count, seg) in enumerate(sorted_segment_list):
        if count > 0:
            total_docs += count
            if total_docs < fib(i + 5):
                reader = SegmentReader(writer.storage, writer.schema, seg)
                writer.add_reader(reader)
                reader.close()
            else:
                newsegments.append(seg)
    return newsegments


def OPTIMIZE(writer, segments):
    """This policy merges all existing segments.
    """

    from whoosh.filedb.filereading import SegmentReader
    for seg in segments:
        reader = SegmentReader(writer.storage, writer.schema, seg)
        writer.add_reader(reader)
        reader.close()
    return SegmentSet()


# Writer object

class SegmentWriter(IndexWriter):
    def __init__(self, ix, poolclass=None, procs=0, blocklimit=128,
                 timeout=0.0, delay=0.1, name=None, **poolargs):
        self.writelock = ix.lock("WRITELOCK")
        if not try_for(self.writelock.acquire, timeout=timeout, delay=delay):
            raise LockError
        
        self.ix = ix
        self.storage = ix.storage
        self.indexname = ix.indexname
        
        info = ix._read_toc()
        self.schema = info.schema
        self.segments = info.segments
        self.blocklimit = blocklimit
        self.segment_number = info.segment_counter + 1
        self.generation = info.generation + 1
        
        self.name = name or "_%s_%s" % (self.indexname, self.segment_number)
        self.docnum = 0
        self.fieldlength_totals = defaultdict(int)
        self._added = False
    
        # Create a temporary segment to use its .*_filename attributes
        segment = Segment(self.name, 0, None, None)
        
        # Terms index
        tf = self.storage.create_file(segment.termsindex_filename)
        self.termsindex = CodedOrderedWriter(tf,
                                             keycoder=misc.encode_termkey,
                                             valuecoder=misc.encode_terminfo)
        
        # Term postings file
        pf = self.storage.create_file(segment.termposts_filename)
        self.postwriter = FilePostingWriter(pf, blocklimit=blocklimit)
        
        if self.schema.has_vectored_fields():
            # Vector index
            vf = self.storage.create_file(segment.vectorindex_filename)
            self.vectorindex = CodedHashWriter(vf,
                                               keycoder=misc.encode_vectorkey,
                                               valuecoder=misc.encode_vectoroffset)
            
            # Vector posting file
            vpf = self.storage.create_file(segment.vectorposts_filename)
            self.vpostwriter = FilePostingWriter(vpf, stringids=True)
        else:
            self.vectorindex = None
            self.vpostwriter = None
        
        # Stored fields file
        sf = self.storage.create_file(segment.storedfields_filename)
        self.storedfields = StoredFieldWriter(sf)
        
        # Field lengths file
        self.lengthfile = self.storage.create_file(segment.fieldlengths_filename)
        
        # Create the pool
        if poolclass is None:
            if procs > 1:
                from whoosh.filedb.multiproc import MultiPool
                poolclass = MultiPool
            else:
                poolclass = TempfilePool
        self.pool = poolclass(self.schema, procs=procs, **poolargs)
        
    def add_field(self, fieldname, fieldspec):
        if self._added:
            raise Exception("Can't modify schema after adding data to writer")
        super(SegmentWriter, self).add_field(fieldname, fieldspec)
    
    def remove_field(self, fieldname):
        if self._added:
            raise Exception("Can't modify schema after adding data to writer")
        super(SegmentWriter, self).remove_field(fieldname)
    
    def delete_document(self, docnum, delete=True):
        """Deletes a document by number.
        """
        self.segments.delete_document(docnum, delete=delete)

    def deleted_count(self):
        """Returns the total number of deleted documents in this index.
        """
        return self.segments.deleted_count()

    def is_deleted(self, docnum):
        """Returns True if a given document number is deleted but
        not yet optimized out of the index.
        """
        return self.segments.is_deleted(docnum)

    def has_deletions(self):
        """Returns True if this index has documents that are marked
        deleted but haven't been optimized out of the index yet.
        """
        return self.segments.has_deletions()
    
    def searcher(self):
        from whoosh.filedb.fileindex import FileIndex
        return FileIndex(self.storage, indexname=self.indexname).searcher()
    
    def add_reader(self, reader):
        startdoc = self.docnum
        
        has_deletions = reader.has_deletions()
        if has_deletions:
            docmap = {}
        
        fieldnames = set(self.schema.names())
        
        # Add stored documents, vectors, and field lengths
        for docnum in xrange(reader.doc_count_all()):
            if (not has_deletions) or (not reader.is_deleted(docnum)):
                d = dict(item for item
                         in reader.stored_fields(docnum).iteritems()
                         if item[0] in fieldnames)
                # We have to append a dictionary for every document, even if
                # it's empty.
                self.storedfields.append(d)
                
                if has_deletions:
                    docmap[docnum] = self.docnum
                
                for fieldname, length in reader.doc_field_lengths(docnum):
                    if fieldname in fieldnames:
                        self.pool.add_field_length(self.docnum, fieldname, length)
                
                for fieldname in reader.vector_names():
                    if (fieldname in fieldnames
                        and reader.has_vector(docnum, fieldname)):
                        vpostreader = reader.vector(docnum, fieldname)
                        self._add_vector_reader(self.docnum, fieldname, vpostreader)
                
                self.docnum += 1
        
        for fieldname, text, _, _ in reader:
            if fieldname in fieldnames:
                postreader = reader.postings(fieldname, text)
                while postreader.is_active():
                    docnum = postreader.id()
                    valuestring = postreader.value()
                    if has_deletions:
                        newdoc = docmap[docnum]
                    else:
                        newdoc = startdoc + docnum
                    
                    self.pool.add_posting(fieldname, text, newdoc,
                                          postreader.weight(), valuestring)
                    postreader.next()
                    
        self._added = True
    
    def add_document(self, **fields):
        schema = self.schema
        
        # Sort the keys
        fieldnames = sorted([name for name in fields.keys()
                             if not name.startswith("_")])
        
        # Check if the caller gave us a bogus field
        for name in fieldnames:
            if name not in schema:
                raise UnknownFieldError("No field named %r in %s" % (name, schema))
        
        self.storedfields
        storedvalues = {}
        
        docnum = self.docnum
        for fieldname in fieldnames:
            value = fields.get(fieldname)
            if value is not None:
                field = schema[fieldname]
                
                if field.indexed:
                    self.pool.add_content(docnum, fieldname, field, value)
                
                vformat = field.vector
                if vformat:
                    vlist = sorted((w, weight, valuestring)
                                   for w, freq, weight, valuestring
                                   in vformat.word_values(value, mode="index"))
                    self._add_vector(docnum, fieldname, vlist)
                
                if field.stored:
                    # Caller can override the stored value by including a key
                    # _stored_<fieldname>
                    storedvalue = value
                    storedname = "_stored_" + fieldname
                    if storedname in fields:
                        storedvalue = fields[storedname]
                    storedvalues[fieldname] = storedvalue
        
        self._added = True
        self.storedfields.append(storedvalues)
        self.docnum += 1
    
    def _add_vector(self, docnum, fieldname, vlist):
        vpostwriter = self.vpostwriter
        offset = vpostwriter.start(self.schema[fieldname].vector)
        for text, weight, valuestring in vlist:
            assert isinstance(text, unicode), "%r is not unicode" % text
            vpostwriter.write(text, weight, valuestring, 0)
        vpostwriter.finish()
        
        self.vectorindex.add((docnum, fieldname), offset)
    
    def _add_vector_reader(self, docnum, fieldname, vreader):
        vpostwriter = self.vpostwriter
        offset = vpostwriter.start(self.schema[fieldname].vector)
        while vreader.is_active():
            # text, weight, valuestring, fieldlen
            vpostwriter.write(vreader.id(), vreader.weight(), vreader.value(), 0)
            vreader.next()
        vpostwriter.finish()
        
        self.vectorindex.add((docnum, fieldname), offset)
    
    def _close_all(self):
        self.termsindex.close()
        self.postwriter.close()
        self.storedfields.close()
        if not self.lengthfile.is_closed:
            self.lengthfile.close()
        if self.vectorindex:
            self.vectorindex.close()
        if self.vpostwriter:
            self.vpostwriter.close()
    
    def _getsegment(self):
        return Segment(self.name, self.docnum,
                       self.pool.fieldlength_totals(),
                       self.pool.fieldlength_maxes())
    
    def commit(self, mergetype=None, optimize=False, merge=True):
        """Finishes writing and saves all additions and changes to disk.
        
        There are four possible ways to use this method::
        
            # Merge small segments but leave large segments, trying to
            # balance fast commits with fast searching:
            writer.commit()
        
            # Merge all segments into a single segment:
            writer.commit(optimize=True)
            
            # Don't merge any existing segments:
            writer.commit(merge=False)
            
            # Use a custom merge function
            writer.commit(mergetype=my_merge_function)
        
        :param mergetype: a custom merge function taking an Index object,
            Writer object, and SegmentSet object as arguments, and returning a
            new SegmentSet object. If you supply a ``mergetype`` function,
            the values of the ``optimize`` and ``merge`` arguments are ignored.
        :param optimize: if True, all existing segments are merged with the
            documents you've added to this writer (and the value of the
            ``merge`` argument is ignored).
        :param merge: if False, do not merge small segments.
        """
        
        if mergetype:
            pass
        elif optimize:
            mergetype = OPTIMIZE
        elif not merge:
            mergetype = NO_MERGE
        else:
            mergetype = MERGE_SMALL
        
        # Call the merge policy function. The policy may choose to merge other
        # segments into this writer's pool
        new_segments = mergetype(self, self.segments)
        
        # Tell the pool we're finished adding information, it should add its
        # accumulated data to the lengths, terms index, and posting files.
        if self._added:
            self.pool.finish(self.docnum, self.lengthfile, self.termsindex,
                             self.postwriter)
        
            # Create a Segment object for the segment created by this writer and
            # add it to the list of remaining segments returned by the merge policy
            # function
            new_segments.append(self._getsegment())
        
        # Close all files, write a new TOC with the new segment list, and
        # release the lock.
        self._close_all()
        
        from whoosh.filedb.fileindex import _write_toc, _clean_files
        _write_toc(self.storage, self.schema, self.indexname, self.generation,
                   self.segment_number, new_segments)
        
        readlock = self.ix.lock("READLOCK")
        readlock.acquire(True)
        try:
            _clean_files(self.storage, self.indexname, self.generation, new_segments)
        finally:
            readlock.release()
        
        self.writelock.release()
        
    def cancel(self):
        self.pool.cancel()
        self._close_all()
        self.writelock.release()




