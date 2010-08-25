import unittest
from os import mkdir
from os.path import exists
from shutil import rmtree

from whoosh import fields, index, query, qparser
from whoosh.filedb.filestore import FileStorage, RamStorage
from whoosh.filedb.filewriting import NO_MERGE


class TestIndexing(unittest.TestCase):
    def make_index(self, dirname, schema, ixname):
        if not exists(dirname):
            mkdir(dirname)
        st = FileStorage(dirname)
        ix = st.create_index(schema, indexname = ixname)
        return ix
    
    def destroy_index(self, dirname):
        if exists(dirname):
            try:
                rmtree(dirname)
            except OSError, e:
                pass
    
    def test_creation(self):
        s = fields.Schema(content=fields.TEXT(phrase = True),
                          title=fields.TEXT(stored = True),
                          path=fields.ID(stored = True),
                          tags=fields.KEYWORD(stored = True),
                          quick=fields.NGRAM,
                          note=fields.STORED)
        st = RamStorage()
        
        ix = st.create_index(s)
        w = ix.writer()
        w.add_document(title=u"First", content=u"This is the first document", path=u"/a",
                       tags=u"first second third", quick=u"First document", note=u"This is the first document")
        w.add_document(content=u"Let's try this again", title=u"Second", path=u"/b",
                       tags=u"Uno Dos Tres", quick=u"Second document", note=u"This is the second document")
        w.commit()
        
    def test_integrity(self):
        s = fields.Schema(name = fields.TEXT, value = fields.TEXT)
        st = RamStorage()
        ix = st.create_index(s)
        
        w = ix.writer()
        w.add_document(name = u"Yellow brown", value = u"Blue red green purple?")
        w.add_document(name = u"Alpha beta", value = u"Gamma delta epsilon omega.")
        w.commit()
        
        w = ix.writer()
        w.add_document(name = u"One two", value = u"Three four five.")
        w.commit()
        
        tr = ix.reader()
        self.assertEqual(ix.doc_count_all(), 3)
        self.assertEqual(list(tr.lexicon("name")), ["alpha", "beta", "brown", "one", "two", "yellow"])
    
    def test_lengths(self):
        s = fields.Schema(f1 = fields.KEYWORD(stored = True, scorable = True),
                          f2 = fields.KEYWORD(stored = True, scorable = True))
        ix = self.make_index("testindex", s, "test_lengths")
        
        try:
            w = ix.writer()
            try:
                tokens = u"ABCDEFG"
                from itertools import cycle, islice
                lengths = [10, 20, 2, 102, 45, 3, 420, 2]
                for length in lengths:
                    w.add_document(f2 = u" ".join(islice(cycle(tokens), length)))
                w.commit()
            except Exception:
                w.cancel()
                raise
                
            dr = ix.reader()
            try:
                ls1 = [dr.doc_field_length(i, "f1") for i in xrange(0, len(lengths))]
                self.assertEqual(ls1, [0]*len(lengths))
                ls2 = [dr.doc_field_length(i, "f2") for i in xrange(0, len(lengths))]
                self.assertEqual(ls2, lengths)
            finally:
                dr.close()
                
        finally:
            ix.close()
            self.destroy_index("testindex")
    
    def test_lengths_ram(self):
        s = fields.Schema(f1 = fields.KEYWORD(stored = True, scorable = True),
                          f2 = fields.KEYWORD(stored = True, scorable = True))
        st = RamStorage()
        ix = st.create_index(s)
        w = ix.writer()
        w.add_document(f1 = u"A B C D E", f2 = u"X Y Z")
        w.add_document(f1 = u"B B B B C D D Q", f2 = u"Q R S T")
        w.add_document(f1 = u"D E F", f2 = u"U V A B C D E")
        w.commit()
        
        dr = ix.reader()
        ls1 = [dr.doc_field_length(i, "f1") for i in xrange(0, 3)]
        ls2 = [dr.doc_field_length(i, "f2") for i in xrange(0, 3)]
        self.assertEqual(dr.stored_fields(0)["f1"], "A B C D E")
        self.assertEqual(dr.doc_field_length(0, "f1"), 5)
        self.assertEqual(dr.doc_field_length(1, "f1"), 8)
        self.assertEqual(dr.doc_field_length(2, "f1"), 3)
        self.assertEqual(dr.doc_field_length(0, "f2"), 3)
        self.assertEqual(dr.doc_field_length(1, "f2"), 4)
        self.assertEqual(dr.doc_field_length(2, "f2"), 7)
        
        self.assertEqual(ix.field_length("f1"), 16)
        self.assertEqual(ix.field_length("f2"), 14)
        
    def test_merged_lengths(self):
        s = fields.Schema(f1 = fields.KEYWORD(stored = True, scorable = True),
                          f2 = fields.KEYWORD(stored = True, scorable = True))
        st = RamStorage()
        ix = st.create_index(s)
        w = ix.writer()
        w.add_document(f1 = u"A B C", f2 = u"X")
        w.add_document(f1 = u"B C D E", f2 = u"Y Z")
        w.commit()
        
        w = ix.writer()
        w.add_document(f1 = u"A", f2 = u"B C D E X Y")
        w.add_document(f1 = u"B C", f2 = u"X")
        w.commit(NO_MERGE)
        
        w = ix.writer()
        w.add_document(f1 = u"A B X Y Z", f2 = u"B C")
        w.add_document(f1 = u"Y X", f2 = u"A B")
        w.commit(NO_MERGE)
        
        dr = ix.reader()
        self.assertEqual(dr.stored_fields(0)["f1"], u"A B C")
        self.assertEqual(dr.doc_field_length(0, "f1"), 3)
        self.assertEqual(dr.doc_field_length(2, "f2"), 6)
        self.assertEqual(dr.doc_field_length(4, "f1"), 5)
        dr.close()
        
    def test_frequency_keyword(self):
        s = fields.Schema(content = fields.KEYWORD)
        st = RamStorage()
        ix = st.create_index(s)
        
        w = ix.writer()
        w.add_document(content = u"A B C D E")
        w.add_document(content = u"B B B B C D D")
        w.add_document(content = u"D E F")
        w.commit()
        
        tr = ix.reader()
        self.assertEqual(tr.doc_frequency("content", u"B"), 2)
        self.assertEqual(tr.frequency("content", u"B"), 5)
        self.assertEqual(tr.doc_frequency("content", u"E"), 2)
        self.assertEqual(tr.frequency("content", u"E"), 2)
        self.assertEqual(tr.doc_frequency("content", u"A"), 1)
        self.assertEqual(tr.frequency("content", u"A"), 1)
        self.assertEqual(tr.doc_frequency("content", u"D"), 3)
        self.assertEqual(tr.frequency("content", u"D"), 4)
        self.assertEqual(tr.doc_frequency("content", u"F"), 1)
        self.assertEqual(tr.frequency("content", u"F"), 1)
        self.assertEqual(tr.doc_frequency("content", u"Z"), 0)
        self.assertEqual(tr.frequency("content", u"Z"), 0)
        self.assertEqual(list(tr), [(0, u"A", 1, 1), (0, u"B", 2, 5),
                                    (0, u"C", 2, 2), (0, u"D", 3, 4),
                                    (0, u"E", 2, 2), (0, u"F", 1, 1)])
        tr.close()
        
    def test_frequency_text(self):
        s = fields.Schema(content = fields.KEYWORD)
        st = RamStorage()
        ix = st.create_index(s)
        
        w = ix.writer()
        w.add_document(content = u"alfa bravo charlie delta echo")
        w.add_document(content = u"bravo bravo bravo bravo charlie delta delta")
        w.add_document(content = u"delta echo foxtrot")
        w.commit()
        
        tr = ix.reader()
        self.assertEqual(tr.doc_frequency("content", u"bravo"), 2)
        self.assertEqual(tr.frequency("content", u"bravo"), 5)
        self.assertEqual(tr.doc_frequency("content", u"echo"), 2)
        self.assertEqual(tr.frequency("content", u"echo"), 2)
        self.assertEqual(tr.doc_frequency("content", u"alfa"), 1)
        self.assertEqual(tr.frequency("content", u"alfa"), 1)
        self.assertEqual(tr.doc_frequency("content", u"delta"), 3)
        self.assertEqual(tr.frequency("content", u"delta"), 4)
        self.assertEqual(tr.doc_frequency("content", u"foxtrot"), 1)
        self.assertEqual(tr.frequency("content", u"foxtrot"), 1)
        self.assertEqual(tr.doc_frequency("content", u"zulu"), 0)
        self.assertEqual(tr.frequency("content", u"zulu"), 0)
        self.assertEqual(list(tr), [(0, u"alfa", 1, 1), (0, u"bravo", 2, 5),
                                    (0, u"charlie", 2, 2), (0, u"delta", 3, 4),
                                    (0, u"echo", 2, 2), (0, u"foxtrot", 1, 1)])
        tr.close()
    
    def test_deletion(self):
        s = fields.Schema(key = fields.ID, name = fields.TEXT, value = fields.TEXT)
        st = RamStorage()
        ix = st.create_index(s)
        
        w = ix.writer()
        w.add_document(key = u"A", name = u"Yellow brown", value = u"Blue red green purple?")
        w.add_document(key = u"B", name = u"Alpha beta", value = u"Gamma delta epsilon omega.")
        w.add_document(key = u"C", name = u"One two", value = u"Three four five.")
        w.commit()
        
        count = ix.delete_by_term("key", u"B")
        self.assertEqual(count, 1)
        ix.commit()
        
        self.assertEqual(ix.doc_count_all(), 3)
        self.assertEqual(ix.doc_count(), 2)
        
        ix.optimize()
        self.assertEqual(ix.doc_count(), 2)
        tr = ix.reader()
        self.assertEqual(list(tr.lexicon("name")), ["brown", "one", "two", "yellow"])
        tr.close()

    def test_update(self):
        # Test update with multiple unique keys
        SAMPLE_DOCS = [{"id": u"test1", "path": u"/test/1", "text": u"Hello"},
                       {"id": u"test2", "path": u"/test/2", "text": u"There"},
                       {"id": u"test3", "path": u"/test/3", "text": u"Reader"},
                       ]
        
        schema = fields.Schema(id=fields.ID(unique=True, stored=True),
                               path=fields.ID(unique=True, stored=True),
                               text=fields.TEXT)
        ix = self.make_index("testindex", schema, "test_update")
        try:
            writer = ix.writer()
            try:
                for doc in SAMPLE_DOCS:
                    writer.add_document(**doc)
                writer.commit()
            except:
                writer.cancel()
                raise
            
            writer = ix.writer()
            try:
                writer.update_document(id=u"test2", path=u"test/1", text=u"Replacement")
                writer.commit()
            except:
                writer.cancel()
                raise
        finally:
            ix.close()
            self.destroy_index("testindex")

    def test_reindex(self):
        SAMPLE_DOCS = [
            {'id': u'test1', 'text': u'This is a document. Awesome, is it not?'},
            {'id': u'test2', 'text': u'Another document. Astounding!'},
            {'id': u'test3', 'text': u'A fascinating article on the behavior of domestic steak knives.'},
        ]

        schema = fields.Schema(text=fields.TEXT(stored=True),
                               id=fields.ID(unique=True, stored=True))
        ix = self.make_index("testindex", schema, "test_reindex")
        try:
            def reindex():
                writer = ix.writer()
                try:
                    for doc in SAMPLE_DOCS:
                        writer.update_document(**doc)
                    writer.commit()
                except:
                    writer.cancel()
                    raise

            reindex()
            self.assertEqual(ix.doc_count_all(), 3)
            reindex()
            self.assertEqual(ix.doc_count_all(), 3)
            
        finally:
            ix.close()
            self.destroy_index("testindex")
            
    def test_noscorables1(self):
        values = [u"alfa", u"bravo", u"charlie", u"delta", u"echo", u"foxtrot",
                  u"golf", u"hotel", u"india", u"juliet", u"kilo", u"lima"]
        from random import choice, sample, randint
        
        times = 1000
        
        schema = fields.Schema(id=fields.ID, tags=fields.KEYWORD)
        ix = self.make_index("testindex", schema, "noscorables1")
        try:
            w = ix.writer()
            for i in xrange(times):
                w.add_document(id=choice(values), tags=u" ".join(sample(values, randint(2, 7))))
            w.commit()
            
            s = ix.searcher()
            r = s.search(query.Term("id", "bravo"))
            s.close()
        finally:
            self.destroy_index("testindex")
            
    def test_noscorables2(self):
        schema = fields.Schema(field=fields.ID)
        ix = self.make_index("testindex", schema, "noscorables2")
        try:
            writer = ix.writer()
            writer.add_document(field=u'foo')
            writer.commit()
        finally:
            self.destroy_index("testindex")
            
    def test_longfield(self):
        schema = fields.Schema(field=fields.TEXT)
        ix = self.make_index("testindex", schema, "longfield")

        import random
        from whoosh.filedb.filewriting import DOCLENGTH_LIMIT
        domain = [u"alfa", u"bravo", u"charlie", u"delta", u"echo", u"foxtrot", u"golf"]
        size = DOCLENGTH_LIMIT + 20
        value = u" ".join(random.choice(domain) for _ in xrange(size))
        
        try:
            writer = ix.writer()
            writer.add_document(field=value)
            writer.commit()
            
            reader = ix.reader()
            self.assertEqual(reader.doc_field_length(0, 0), DOCLENGTH_LIMIT)
        finally:
            self.destroy_index("testindex")


if __name__ == '__main__':
    unittest.main()
