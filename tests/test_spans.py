import unittest

from whoosh import analysis, fields, spans
from whoosh.filedb.filestore import RamStorage
from whoosh.query import And, Or, Term
from whoosh.util import permutations


class TestSpans(unittest.TestCase):
    domain = ("alfa", "bravo", "bravo", "charlie", "delta", "echo")
    
    def get_index(self):
        if hasattr(self, "_ix"):
            return self._ix
        
        ana = analysis.SimpleAnalyzer()
        schema = fields.Schema(text=fields.TEXT(analyzer=ana, stored=True))
        st = RamStorage()
        ix = st.create_index(schema)
        
        w = ix.writer()
        for ls in permutations(self.domain, 4):
            w.add_document(text=u" ".join(ls), _stored_text=ls)
        w.commit()
        
        self._ix = ix
        return ix
        
    
    def test_span_term(self):
        ix = self.get_index()
        s = ix.searcher()
        
        alllists = [d["text"] for d in s.all_stored_fields()]
        
        for word in self.domain:
            q = Term("text", word)
            m = q.matcher(s)
            
            ids = set()
            while m.is_active():
                id = m.id()
                sps = m.spans()
                ids.add(id)
                original = s.stored_fields(id)["text"]
                self.assertTrue(word in original, "%r not in %r" % (word, original))
                
                if word != "bravo":
                    self.assertEqual(len(sps), 1)
                self.assertEqual(original.index(word), sps[0].start)
                self.assertEqual(original.index(word), sps[0].end)
                m.next()
        
            for i, ls in enumerate(alllists):
                if word in ls:
                    self.assertTrue(i in ids)
                else:
                    self.assertFalse(i in ids)
                    
    def test_span_first(self):
        ix = self.get_index()
        s = ix.searcher()
        
        for word in self.domain:
            q = spans.SpanFirst(Term("text", word))
            m = q.matcher(s)
            while m.is_active():
                sps = m.spans()
                original = s.stored_fields(m.id())["text"]
                self.assertEqual(original[0], word)
                self.assertEqual(len(sps), 1)
                self.assertEqual(sps[0].start, 0)
                self.assertEqual(sps[0].end, 0)
                m.next()
                
        q = spans.SpanFirst(Term("text", "bravo"), limit=1)
        m = q.matcher(s)
        while m.is_active():
            orig = s.stored_fields(m.id())["text"]
            for sp in m.spans():
                self.assertEqual(orig[sp.start], "bravo")
            m.next()
            
    def test_span_near(self):
        ix = self.get_index()
        s = ix.searcher()
        
        def test(q):
            m = q.matcher(s)
            while m.is_active():
                yield s.stored_fields(m.id())["text"], m.spans()
                m.next()
                
        for orig, sps in test(spans.SpanNear(Term("text", "alfa"), Term("text", "bravo"), ordered=True)):
            self.assertEqual(orig[sps[0].start], "alfa")
            self.assertEqual(orig[sps[0].end], "bravo")
            
        for orig, sps in test(spans.SpanNear(Term("text", "alfa"), Term("text", "bravo"), ordered=False)):
            first = orig[sps[0].start]
            second = orig[sps[0].end]
            self.assertTrue((first == "alfa" and second == "bravo")
                            or (first == "bravo" and second == "alfa"))
            
        for orig, sps in test(spans.SpanNear(Term("text", "bravo"), Term("text", "bravo"), ordered=True)):
            text = " ".join(orig)
            self.assertTrue(text.find("bravo bravo") > -1)
            
        q = spans.SpanNear(spans.SpanNear(Term("text", "alfa"), Term("text", "charlie")), Term("text", "echo"))
        for orig, sps in test(q):
            text = " ".join(orig)
            self.assertTrue(text.find("alfa charlie echo") > -1)
            
        q = spans.SpanNear(Or([Term("text", "alfa"), Term("text", "charlie")]), Term("text", "echo"), ordered=True)
        for orig, sps in test(q):
            text = " ".join(orig)
            self.assertTrue(text.find("alfa echo") > -1 or text.find("charlie echo") > -1)
    
    def test_near_unordered(self):
        schema = fields.Schema(text=fields.TEXT(stored=True))
        st = RamStorage()
        ix = st.create_index(schema)
        w = ix.writer()
        w.add_document(text=u"alfa bravo charlie delta echo")
        w.add_document(text=u"alfa bravo delta echo charlie")
        w.add_document(text=u"alfa charlie bravo delta echo")
        w.add_document(text=u"echo delta alfa foxtrot")
        w.commit()
        
        s = ix.searcher()
        q = spans.SpanNear(Term("text", "bravo"), Term("text", "charlie"), ordered=False)
        r = sorted(d["text"] for d in s.search(q))
        self.assertEqual(r, [u'alfa bravo charlie delta echo',
                             u'alfa charlie bravo delta echo'])
        
    def test_span_near2(self):
        ana = analysis.SimpleAnalyzer()
        schema = fields.Schema(text=fields.TEXT(analyzer=ana, stored=True))
        st = RamStorage()
        ix = st.create_index(schema)
        w = ix.writer()
        w.add_document(text=u"The Lucene library is by Doug Cutting and Whoosh was made by Matt Chaput")
        w.commit()
        
        nq1 = spans.SpanNear(Term("text", "lucene"), Term("text", "doug"), slop=5)
        nq2 = spans.SpanNear(nq1, Term("text", "whoosh"), slop=4)
        
        s = ix.searcher()
        m = nq2.matcher(s)
        self.assertEqual(m.spans(), [spans.Span(1, 8)])
        
    def test_span_not(self):
        ix = self.get_index()
        s = ix.searcher()
        
        nq = spans.SpanNear(Term("text", "alfa"), Term("text", "charlie"), slop=2)
        bq = Term("text", "bravo")
        q = spans.SpanNot(nq, bq)
        m = q.matcher(s)
        while m.is_active():
            orig = s.stored_fields(m.id())["text"]
            i1 = orig.index("alfa")
            i2 = orig.index("charlie")
            dist = i2 - i1
            self.assertTrue(dist > 0 and dist < 3)
            if "bravo" in orig:
                self.assertTrue(orig.index("bravo") != i1 + 1)
            m.next()
            
    def test_span_or(self):
        ix = self.get_index()
        s = ix.searcher()
        
        nq = spans.SpanNear(Term("text", "alfa"), Term("text", "charlie"), slop=2)
        bq = Term("text", "bravo")
        q = spans.SpanOr([nq, bq])
        m = q.matcher(s)
        while m.is_active():
            orig = s.stored_fields(m.id())["text"]
            self.assertTrue(("alfa" in orig and "charlie" in orig) or "bravo" in orig)
            m.next()

    def test_span_contains(self):
        ix = self.get_index()
        s = ix.searcher()
        
        nq = spans.SpanNear(Term("text", "alfa"), Term("text", "charlie"), slop=3)
        cq = spans.SpanContains(nq, Term("text", "echo"))
        
        m = cq.matcher(s)
        ls = []
        while m.is_active():
            orig = s.stored_fields(m.id())["text"]
            ls.append(" ".join(orig))
            m.next()
        ls.sort()
        self.assertEqual(ls, ['alfa bravo echo charlie', 'alfa bravo echo charlie',
                              'alfa delta echo charlie', 'alfa echo bravo charlie',
                              'alfa echo bravo charlie', 'alfa echo charlie bravo',
                              'alfa echo charlie bravo', 'alfa echo charlie delta',
                              'alfa echo delta charlie', 'bravo alfa echo charlie',
                              'bravo alfa echo charlie', 'delta alfa echo charlie'])

    def test_span_before(self):
        ix = self.get_index()
        s = ix.searcher()
        
        bq = spans.SpanBefore(Term("text", "alfa"), Term("text", "charlie"))
        m = bq.matcher(s)
        while m.is_active():
            orig = s.stored_fields(m.id())["text"]
            self.assertTrue("alfa" in orig)
            self.assertTrue("charlie" in orig)
            self.assertTrue(orig.index("alfa") < orig.index("charlie"))
            m.next()






            
if __name__ == '__main__':
    unittest.main()











