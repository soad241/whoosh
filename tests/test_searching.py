import unittest

from whoosh import analysis, fields, formats, index, qparser, query, searching, scoring
from whoosh.filedb.filestore import RamStorage
from whoosh.query import *
from whoosh.searching import Searcher
from whoosh.scoring import FieldSorter

class TestSearching(unittest.TestCase):
    def make_index(self):
        s = fields.Schema(key = fields.ID(stored = True),
                          name = fields.TEXT,
                          value = fields.TEXT)
        st = RamStorage()
        ix = st.create_index(s)
        
        w = ix.writer()
        w.add_document(key = u"A", name = u"Yellow brown", value = u"Blue red green render purple?")
        w.add_document(key = u"B", name = u"Alpha beta", value = u"Gamma delta epsilon omega.")
        w.add_document(key = u"C", name = u"One two", value = u"Three rendered four five.")
        w.add_document(key = u"D", name = u"Quick went", value = u"Every red town.")
        w.add_document(key = u"E", name = u"Yellow uptown", value = u"Interest rendering outer photo!")
        w.commit()
        
        return ix
    
    def _get_keys(self, stored_fields):
        return sorted([d.get("key") for d in stored_fields])
    
    def _docs(self, q, s):
        return self._get_keys([s.stored_fields(docnum) for docnum
                               in q.docs(s)])
    
    def _doc_scores(self, q, s, w):
        return self._get_keys([s.stored_fields(docnum) for docnum, score
                               in q.doc_scores(s, weighting = w)])
    
    def test_empty_index(self):
        schema = fields.Schema(key = fields.ID(stored=True), value = fields.TEXT)
        st = RamStorage()
        self.assertRaises(index.EmptyIndexError, st.open_index, schema)
    
    def test_docs_method(self):
        ix = self.make_index()
        s = ix.searcher()
        
        self.assertEqual(self._get_keys(s.documents(name = "yellow")), [u"A", u"E"])
        self.assertEqual(self._get_keys(s.documents(value = "red")), [u"A", u"D"])
    
    def _run_query(self, q, result):
        ix = self.make_index()
        s = ix.searcher()
        self.assertEqual(self._docs(q, s), result)
        
    def test_term(self):
        self._run_query(Term("name", u"yellow"), [u"A", u"E"])
        self._run_query(Term("value", u"zeta"), [])
        self._run_query(Term("value", u"red"), [u"A", u"D"])
        
    def test_require(self):
        self._run_query(Require(Term("value", u"red"), Term("name", u"yellow")),
                        [u"A"])
        
    def test_and(self):
        self._run_query(And([Term("value", u"red"), Term("name", u"yellow")]),
                        [u"A"])
        
    def test_or(self):
        self._run_query(Or([Term("value", u"red"), Term("name", u"yellow")]),
                        [u"A", u"D", u"E"])
    
    def test_or_minmatch(self):
        schema = fields.Schema(k=fields.STORED, v=fields.TEXT)
        st = RamStorage()
        ix = st.create_index(schema)
        
        w = ix.writer()
        w.add_document(k=1, v=u"alfa bravo charlie delta echo")
        w.add_document(k=2, v=u"bravo charlie delta echo foxtrot")
        w.add_document(k=3, v=u"charlie delta echo foxtrot golf")
        w.add_document(k=4, v=u"delta echo foxtrot golf hotel")
        w.add_document(k=5, v=u"echo foxtrot golf hotel india")
        w.add_document(k=6, v=u"foxtrot golf hotel india juliet")
        w.commit()
        
        s = ix.searcher()
        q = Or([Term("v", "echo"), Term("v", "foxtrot")], minmatch=2)
        r = s.search(q)
        self.assertEqual(sorted(d["k"] for d in r), [2, 3, 4, 5])
    
    def test_not(self):
        self._run_query(Or([Term("value", u"red"), Term("name", u"yellow"), Not(Term("name", u"quick"))]),
                        [u"A", u"E"])
    
    def test_not2(self):
        schema = fields.Schema(name=fields.ID(stored=True), value=fields.TEXT)
        storage = RamStorage()
        ix = storage.create_index(schema)
        writer = ix.writer()
        writer.add_document(name=u"a", value=u"alfa bravo charlie delta echo")
        writer.add_document(name=u"b", value=u"bravo charlie delta echo foxtrot")
        writer.add_document(name=u"c", value=u"charlie delta echo foxtrot golf")
        writer.add_document(name=u"d", value=u"delta echo golf hotel india")
        writer.add_document(name=u"e", value=u"echo golf hotel india juliet")
        writer.commit()
        
        searcher = ix.searcher()
        p = qparser.QueryParser("value")
        results = searcher.search(p.parse("echo NOT golf"))
        self.assertEqual(sorted([d["name"] for d in results]), ["a", "b"])
        
        results = searcher.search(p.parse("echo NOT bravo"))
        self.assertEqual(sorted([d["name"] for d in results]), ["c", "d", "e"])
        searcher.close()
        
        ix.delete_by_term("value", u"bravo")
        ix.commit()
        
        searcher = ix.searcher()
        results = searcher.search(p.parse("echo NOT charlie"))
        self.assertEqual(sorted([d["name"] for d in results]), ["d", "e"])
        searcher.close()
    
    def test_andnot(self):
        self._run_query(AndNot(Term("name", u"yellow"), Term("value", u"purple")),
                        [u"E"])
    
    def test_variations(self):
        self._run_query(Variations("value", u"render"), [u"A", u"C", u"E"])
    
    def test_topnot(self):
        self._run_query(Not(Term("name", "yellow")), [u"B", u"C", u"D"])
    
    def test_wildcard(self):
        self._run_query(Or([Wildcard('value', u'*red*'), Wildcard('name', u'*yellow*')]),
                        [u"A", u"C", u"D", u"E"])
    
#        for wcls in dir(scoring):
#            if wcls is scoring.Weighting: continue
#            if isinstance(wcls, scoring.Weighting):
#                for query, result in tests:
#                    self.assertEqual(self._doc_scores(query, s, wcls), result)
    
    def test_range(self):
        schema = fields.Schema(id=fields.ID(stored=True), content=fields.TEXT)
        st = RamStorage()
        ix = st.create_index(schema)
        
        w = ix.writer()
        w.add_document(id=u"A", content=u"alfa bravo charlie delta echo")
        w.add_document(id=u"B", content=u"bravo charlie delta echo foxtrot")
        w.add_document(id=u"C", content=u"charlie delta echo foxtrot golf")
        w.add_document(id=u"D", content=u"delta echo foxtrot golf hotel")
        w.add_document(id=u"E", content=u"echo foxtrot golf hotel india")
        w.commit()
        s = ix.searcher()
        qp = qparser.QueryParser("content", schema=schema)
        
        q = qp.parse(u"charlie [delta TO foxtrot]")
        self.assertEqual(q.__class__.__name__, "And")
        self.assertEqual(q[0].__class__.__name__, "Term")
        self.assertEqual(q[1].__class__.__name__, "TermRange")
        self.assertEqual(q[1].start, "delta")
        self.assertEqual(q[1].end, "foxtrot")
        self.assertEqual(q[1].startexcl, False)
        self.assertEqual(q[1].endexcl, False)
        ids = sorted([d['id'] for d in s.search(q)])
        self.assertEqual(ids, [u'A', u'B', u'C'])
        
        q = qp.parse(u"foxtrot {echo TO hotel]")
        self.assertEqual(q.__class__.__name__, "And")
        self.assertEqual(q[0].__class__.__name__, "Term")
        self.assertEqual(q[1].__class__.__name__, "TermRange")
        self.assertEqual(q[1].start, "echo")
        self.assertEqual(q[1].end, "hotel")
        self.assertEqual(q[1].startexcl, True)
        self.assertEqual(q[1].endexcl, False)
        ids = sorted([d['id'] for d in s.search(q)])
        self.assertEqual(ids, [u'B', u'C', u'D', u'E'])
        
        q = qp.parse(u"{bravo TO delta}")
        self.assertEqual(q.__class__.__name__, "TermRange")
        self.assertEqual(q.start, "bravo")
        self.assertEqual(q.end, "delta")
        self.assertEqual(q.startexcl, True)
        self.assertEqual(q.endexcl, True)
        ids = sorted([d['id'] for d in s.search(q)])
        self.assertEqual(ids, [u'A', u'B', u'C'])
    
    def test_range_clusiveness(self):
        schema = fields.Schema(id=fields.ID(stored=True))
        st = RamStorage()
        ix = st.create_index(schema)
        w = ix.writer()
        for letter in u"abcdefg":
            w.add_document(id=letter)
        w.commit()
        s = ix.searcher()
        
        def do(startexcl, endexcl, string):
            q = TermRange("id", "b", "f", startexcl, endexcl)
            r = "".join(sorted(d['id'] for d in s.search(q)))
            self.assertEqual(r, string)
            
        do(False, False, "bcdef")
        do(True, False, "cdef")
        do(True, True, "cde")
        do(False, True, "bcde")
        
    def test_open_ranges(self):
        schema = fields.Schema(id=fields.ID(stored=True))
        st = RamStorage()
        ix = st.create_index(schema)
        w = ix.writer()
        for letter in u"abcdefg":
            w.add_document(id=letter)
        w.commit()
        s = ix.searcher()
        
        qp = qparser.QueryParser("id", schema=schema)
        def do(qstring, result):
            q = qp.parse(qstring)
            r = "".join(sorted([d['id'] for d in s.search(q)]))
            self.assertEqual(r, result)
            
        do(u"[b TO]", "bcdefg")
        do(u"[TO e]", "abcde")
        do(u"[b TO d]", "bcd")
        do(u"{b TO]", "cdefg")
        do(u"[TO e}", "abcd")
        do(u"{b TO d}", "c")
    
    def test_keyword_or(self):
        schema = fields.Schema(a=fields.ID(stored=True), b=fields.KEYWORD)
        st = RamStorage()
        ix = st.create_index(schema)
        
        w = ix.writer()
        w.add_document(a=u"First", b=u"ccc ddd")
        w.add_document(a=u"Second", b=u"aaa ddd")
        w.add_document(a=u"Third", b=u"ccc eee")
        w.commit()
        
        qp = qparser.QueryParser("b", schema=schema)
        searcher = ix.searcher()
        qr = qp.parse(u"b:ccc OR b:eee")
        self.assertEqual(qr.__class__, query.Or)
        r = searcher.search(qr)
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0]["a"], "Third")
        self.assertEqual(r[1]["a"], "First")

    def test_merged(self):
        sc = fields.Schema(id=fields.ID(stored=True), content=fields.TEXT)
        st = RamStorage()
        ix = st.create_index(sc)
        w = ix.writer()
        w.add_document(id=u"alfa", content=u"alfa")
        w.add_document(id=u"bravo", content=u"bravo")
        w.add_document(id=u"charlie", content=u"charlie")
        w.add_document(id=u"delta", content=u"delta")
        w.commit()
        
        s = ix.searcher()
        r = s.search(query.Term("content", u"bravo"))
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["id"], "bravo")
        
        w = ix.writer()
        w.add_document(id=u"echo", content=u"echo")
        w.commit()
        self.assertEqual(len(ix.segments), 1)
        
        s = ix.searcher()
        r = s.search(query.Term("content", u"bravo"))
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["id"], "bravo")
        
    def test_multireader(self):
        sc = fields.Schema(id=fields.ID(stored=True), content=fields.TEXT)
        st = RamStorage()
        ix = st.create_index(sc)
        w = ix.writer()
        w.add_document(id=u"alfa", content=u"alfa")
        w.add_document(id=u"bravo", content=u"bravo")
        w.add_document(id=u"charlie", content=u"charlie")
        w.add_document(id=u"delta", content=u"delta")
        w.add_document(id=u"echo", content=u"echo")
        w.add_document(id=u"foxtrot", content=u"foxtrot")
        w.add_document(id=u"golf", content=u"golf")
        w.add_document(id=u"hotel", content=u"hotel")
        w.add_document(id=u"india", content=u"india")
        w.commit()
        
        s = ix.searcher()
        r = s.search(query.Term("content", u"bravo"))
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["id"], "bravo")
        
        w = ix.writer()
        w.add_document(id=u"juliet", content=u"juliet")
        w.add_document(id=u"kilo", content=u"kilo")
        w.add_document(id=u"lima", content=u"lima")
        w.add_document(id=u"mike", content=u"mike")
        w.add_document(id=u"november", content=u"november")
        w.add_document(id=u"oscar", content=u"oscar")
        w.add_document(id=u"papa", content=u"papa")
        w.add_document(id=u"quebec", content=u"quebec")
        w.add_document(id=u"romeo", content=u"romeo")
        w.commit()
        self.assertEqual(len(ix.segments), 2)
        
        r = ix.reader()
        self.assertEqual(r.__class__.__name__, "MultiReader")
        pr = r.postings("content", u"bravo")
        s = ix.searcher()
        r = s.search(query.Term("content", u"bravo"))
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["id"], "bravo")

    def test_score_retrieval(self):
        schema = fields.Schema(title=fields.TEXT(stored=True),
                               content=fields.TEXT(stored=True))
        storage = RamStorage()
        ix = storage.create_index(schema)
        writer = ix.writer()
        writer.add_document(title=u"Miss Mary",
                            content=u"Mary had a little white lamb its fleece was white as snow")
        writer.add_document(title=u"Snow White",
                            content=u"Snow white lived in the forrest with seven dwarfs")
        writer.commit()
        
        searcher = ix.searcher()
        results = searcher.search(Term("content", "white"))
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['title'], u"Miss Mary")
        self.assertEqual(results[1]['title'], u"Snow White")
        self.assertNotEqual(results.score(0), None)
        self.assertNotEqual(results.score(0), 0)
        self.assertNotEqual(results.score(0), 1)

    def test_posting_phrase(self):
        schema = fields.Schema(name=fields.ID(stored=True), value=fields.TEXT)
        storage = RamStorage()
        ix = storage.create_index(schema)
        writer = ix.writer()
        writer.add_document(name=u"A", value=u"Little Miss Muffet sat on a tuffet")
        writer.add_document(name=u"B", value=u"Miss Little Muffet tuffet")
        writer.add_document(name=u"C", value=u"Miss Little Muffet tuffet sat")
        writer.add_document(name=u"D", value=u"Gibberish blonk falunk miss muffet sat tuffet garbonzo")
        writer.add_document(name=u"E", value=u"Blah blah blah pancakes")
        writer.commit()
        
        searcher = ix.searcher()
        
        def names(results):
            return sorted([fields['name'] for fields in results])
        
        q = query.Phrase("value", [u"little", u"miss", u"muffet", u"sat", u"tuffet"])
        sc = q.scorer(searcher)
        self.assertEqual(sc.__class__.__name__, "PostingPhraseScorer")
        
        self.assertEqual(names(searcher.search(q)), ["A"])
        
        q = query.Phrase("value", [u"miss", u"muffet", u"sat", u"tuffet"])
        self.assertEqual(names(searcher.search(q)), ["A", "D"])
        
        q = query.Phrase("value", [u"falunk", u"gibberish"])
        self.assertEqual(names(searcher.search(q)), [])
        
        q = query.Phrase("value", [u"gibberish", u"falunk"], slop=2)
        self.assertEqual(names(searcher.search(q)), ["D"])
        
        #q = query.Phrase("value", [u"blah"] * 4)
        #self.assertEqual(names(searcher.search(q)), []) # blah blah blah blah
        
        q = query.Phrase("value", [u"blah"] * 3)
        self.assertEqual(names(searcher.search(q)), ["E"])
    
    def test_vector_phrase(self):
        ana = analysis.StandardAnalyzer()
        ftype = fields.FieldType(formats.Frequency(ana), formats.Positions(ana), scorable=True)
        schema = fields.Schema(name=fields.ID(stored=True), value=ftype)
        storage = RamStorage()
        ix = storage.create_index(schema)
        writer = ix.writer()
        writer.add_document(name=u"A", value=u"Little Miss Muffet sat on a tuffet")
        writer.add_document(name=u"B", value=u"Miss Little Muffet tuffet")
        writer.add_document(name=u"C", value=u"Miss Little Muffet tuffet sat")
        writer.add_document(name=u"D", value=u"Gibberish blonk falunk miss muffet sat tuffet garbonzo")
        writer.add_document(name=u"E", value=u"Blah blah blah pancakes")
        writer.commit()
        
        searcher = ix.searcher()
        
        def names(results):
            return sorted([fields['name'] for fields in results])
        
        q = query.Phrase("value", [u"little", u"miss", u"muffet", u"sat", u"tuffet"])
        sc = q.scorer(searcher)
        self.assertEqual(sc.__class__.__name__, "VectorPhraseScorer")
        
        self.assertEqual(names(searcher.search(q)), ["A"])
        
        q = query.Phrase("value", [u"miss", u"muffet", u"sat", u"tuffet"])
        self.assertEqual(names(searcher.search(q)), ["A", "D"])
        
        q = query.Phrase("value", [u"falunk", u"gibberish"])
        self.assertEqual(names(searcher.search(q)), [])
        
        q = query.Phrase("value", [u"gibberish", u"falunk"], slop=2)
        self.assertEqual(names(searcher.search(q)), ["D"])
        
        #q = query.Phrase("value", [u"blah"] * 4)
        #self.assertEqual(names(searcher.search(q)), []) # blah blah blah blah
        
        q = query.Phrase("value", [u"blah"] * 3)
        self.assertEqual(names(searcher.search(q)), ["E"])
        
    def test_phrase_score(self):
        schema = fields.Schema(name=fields.ID(stored=True), value=fields.TEXT)
        storage = RamStorage()
        ix = storage.create_index(schema)
        writer = ix.writer()
        writer.add_document(name=u"A", value=u"Little Miss Muffet sat on a tuffet")
        writer.add_document(name=u"D", value=u"Gibberish blonk falunk miss muffet sat tuffet garbonzo")
        writer.add_document(name=u"E", value=u"Blah blah blah pancakes")
        writer.add_document(name=u"F", value=u"Little miss muffet little miss muffet")
        writer.commit()
        
        searcher = ix.searcher()
        q = query.Phrase("value", [u"little", u"miss", u"muffet"])
        sc = q.scorer(searcher)
        self.assertEqual(sc.id, 0)
        score1 = sc.score()
        self.assert_(score1 > 0)
        sc.next()
        self.assertEqual(sc.id, 3)
        self.assert_(sc.score() > score1)

    def test_stop_phrase(self):
        schema = fields.Schema(title=fields.TEXT(stored=True))
        storage = RamStorage()
        ix = storage.create_index(schema)
        writer = ix.writer()
        writer.add_document(title=u"Richard of York")
        writer.add_document(title=u"Lily the Pink")
        writer.commit()
        
        s = ix.searcher()
        qp = qparser.QueryParser("title", schema=schema)
        q = qp.parse(u"richard of york")
        self.assertEqual(len(s.search(q)), 1)
        #q = qp.parse(u"lily the pink")
        #self.assertEqual(len(s.search(q)), 1)
        self.assertEqual(len(s.find("title", u"lily the pink")), 1)
        
    def test_missing_field_scoring(self):
        schema = fields.Schema(name=fields.TEXT(stored=True),
                               hobbies=fields.TEXT(stored=True))
        storage = RamStorage()
        idx = storage.create_index(schema)
        writer = idx.writer() 
        writer.add_document(name=u'Frank', hobbies=u'baseball, basketball')
        writer.commit()
        self.assertEqual(idx.segments[0].field_length(0), 2) # hobbies
        self.assertEqual(idx.segments[0].field_length(1), 1) # name
        
        writer = idx.writer()
        writer.add_document(name=u'Jonny') 
        writer.commit()
        self.assertEqual(len(idx.segments), 1)
        self.assertEqual(idx.segments[0].field_length(0), 2) # hobbies
        self.assertEqual(idx.segments[0].field_length(1), 2) # name
        
        reader = idx.reader()
        searcher = Searcher(reader)
        parser = qparser.MultifieldParser(['name', 'hobbies'], schema=schema)
        q = parser.parse(u"baseball")
        result = searcher.search(q)
        self.assertEqual(len(result), 1)
        
    def test_search_fieldname_underscores(self):
        s = fields.Schema(my_name=fields.ID(stored=True), my_value=fields.TEXT)
        st = RamStorage()
        ix = st.create_index(s)
        
        w = ix.writer()
        w.add_document(my_name=u"Green", my_value=u"It's not easy being green")
        w.add_document(my_name=u"Red", my_value=u"Hopping mad like a playground ball")
        w.commit()
        
        qp = qparser.QueryParser("my_value", schema=s)
        s = ix.searcher()
        r = s.search(qp.parse(u"my_name:Green"))
        self.assertEqual(r[0]['my_name'], "Green")
        s.close()
        ix.close()
        
    def test_short_prefix(self):
        s = fields.Schema(name=fields.ID, value=fields.TEXT)
        qp = qparser.QueryParser("value", schema=s)
        q = qp.parse(u"s*")
        self.assertEqual(q.__class__.__name__, "Prefix")
        self.assertEqual(q.text, "s")
        
    def test_sortedby(self):
        schema = fields.Schema(a=fields.ID(stored=True), b=fields.KEYWORD)
        st = RamStorage()
        ix = st.create_index(schema)

        w = ix.writer()
        w.add_document(a=u"First", b=u"ccc ddd")
        w.add_document(a=u"Second", b=u"aaa ddd")
        w.add_document(a=u"Third", b=u"ccc eee")
        w.commit()

        qp = qparser.QueryParser("b", schema=schema)
        searcher = ix.searcher()
        qr = qp.parse(u"b:ccc")
        self.assertEqual(qr.__class__, query.Term)
        r = searcher.search(qr, sortedby='a')
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0]["a"], "First")
        self.assertEqual(r[1]["a"], "Third")
        
    def test_multisort(self):
        schema = fields.Schema(a=fields.ID(stored=True), b=fields.KEYWORD(stored=True))
        st = RamStorage()
        ix = st.create_index(schema)
        
        w = ix.writer()
        w.add_document(a=u"bravo", b=u"romeo")
        w.add_document(a=u"alfa", b=u"tango")
        w.add_document(a=u"bravo", b=u"india")
        w.add_document(a=u"alfa", b=u"juliet")
        w.commit()
        
        q = Or([Term("a", u"alfa"), Term("a", u"bravo")])
        searcher = ix.searcher()
        r = searcher.search(q, sortedby=('a', 'b'))
        self.assertEqual(r[0]['b'], "juliet")
        self.assertEqual(r[1]['b'], "tango")
        self.assertEqual(r[2]['b'], "india")
        self.assertEqual(r[3]['b'], "romeo")
        
    def test_keysort(self):
        from whoosh.util import natural_key
        self.assertEqual(natural_key("Hi100there2"), ('hi', 100, 'there', 2))
        schema = fields.Schema(a=fields.ID(stored=True))
        st = RamStorage()
        ix = st.create_index(schema)
        
        w = ix.writer()
        w.add_document(a=u"b100x")
        w.add_document(a=u"b5x")
        w.add_document(a=u"100b5x")
        w.commit()
        
        q = Or([Term("a", u"b100x"), Term("a", u"b5x"), Term("a", u"100b5x")])
        searcher = ix.searcher()
        sorter = FieldSorter("a", key=natural_key)
        r = searcher.search(q, sortedby=sorter)
        self.assertEqual(r[0]['a'], "100b5x")
        self.assertEqual(r[1]['a'], "b5x")
        self.assertEqual(r[2]['a'], "b100x")
    
    def test_resultcopy(self):
        schema = fields.Schema(a=fields.TEXT(stored=True))
        st = RamStorage()
        ix = st.create_index(schema)
        
        w = ix.writer()
        w.add_document(a=u"alfa bravo charlie")
        w.add_document(a=u"bravo charlie delta")
        w.add_document(a=u"charlie delta echo")
        w.add_document(a=u"delta echo foxtrot")
        w.commit()
        
        s = ix.searcher()
        r = s.search(qparser.QueryParser("a").parse(u"charlie"))
        self.assertEqual(len(r), 3)
        rcopy = r.copy()
        self.assertEqual(r.scored_list, rcopy.scored_list)
        self.assertEqual(r.scores, rcopy.scores)
        self.assertEqual(r.docs, rcopy.docs)
        self.assert_(r.docs is not rcopy.docs)
        
    def test_weighting(self):
        from whoosh.scoring import Weighting
        
        schema = fields.Schema(id=fields.ID(stored=True),
                               n_comments=fields.ID(stored=True))
        st = RamStorage()
        ix = st.create_index(schema)
        
        w = ix.writer()
        w.add_document(id=u"1", n_comments=u"5")
        w.add_document(id=u"2", n_comments=u"12")
        w.add_document(id=u"3", n_comments=u"2")
        w.add_document(id=u"4", n_comments=u"7")
        w.commit()
        
        class CommentWeighting(Weighting):
            def score(self, searcher, fieldnum, text, docnum, weight, QTF=1):
                ncomments = int(searcher.stored_fields(docnum).get("n_comments", "0"))
                return ncomments
        
        s = ix.searcher(weighting=CommentWeighting())
        r = s.search(qparser.QueryParser("id").parse("[1 TO 4]"))
        ids = [fs["id"] for fs in r]
        self.assertEqual(ids, ["2", "4", "1", "3"])
    
    def test_dismax(self):
        schema = fields.Schema(id=fields.STORED, f1=fields.TEXT, f2=fields.TEXT, f3=fields.TEXT)
        st = RamStorage()
        ix = st.create_index(schema)
        
        w = ix.writer()
        w.add_document(id=1, f1=u"alfa bravo charlie delta", f2=u"alfa alfa alfa",
                       f3 = u"alfa echo foxtrot hotel india")
        w.commit()
        
        s = ix.searcher(weighting=scoring.Frequency())
        qs = [Term("f1", "alfa"), Term("f2", "alfa"), Term("f3", "alfa")]
        r = s.search(DisjunctionMax(qs))
        self.assertEqual(r.score(0), 3.0)
        r = s.search(DisjunctionMax(qs, tiebreak=0.5))
        self.assertEqual(r.score(0), 3.0 + 0.5 + 1.5 + 0.5)
    
    def test_finalweighting(self):
        from whoosh.scoring import Weighting
        
        schema = fields.Schema(id=fields.ID(stored=True),
                               summary=fields.TEXT,
                               n_comments=fields.ID(stored=True))
        st = RamStorage()
        ix = st.create_index(schema)
        
        w = ix.writer()
        w.add_document(id=u"1", summary=u"alfa bravo", n_comments=u"5")
        w.add_document(id=u"2", summary=u"alfa", n_comments=u"12")
        w.add_document(id=u"3", summary=u"bravo", n_comments=u"2")
        w.add_document(id=u"4", summary=u"bravo bravo", n_comments=u"7")
        w.commit()
        
        class CommentWeighting(Weighting):
            def score(self, *args, **kwargs):
                return 0
            
            def final(self, searcher, docnum, score):
                ncomments = int(searcher.stored_fields(docnum).get("n_comments"))
                return ncomments
        
        s = ix.searcher(weighting=CommentWeighting())
        r = s.search(qparser.QueryParser("summary").parse("alfa OR bravo"))
        ids = [fs["id"] for fs in r]
        self.assertEqual(ids, ["2", "4", "1", "3"])
        
    def test_pages(self):
        from whoosh.scoring import Frequency
        
        schema = fields.Schema(id=fields.ID(stored=True), c=fields.TEXT)
        st = RamStorage()
        ix = st.create_index(schema)
        
        w = ix.writer()
        w.add_document(id=u"1", c=u"alfa alfa alfa alfa alfa alfa")
        w.add_document(id=u"2", c=u"alfa alfa alfa alfa alfa")
        w.add_document(id=u"3", c=u"alfa alfa alfa alfa")
        w.add_document(id=u"4", c=u"alfa alfa alfa")
        w.add_document(id=u"5", c=u"alfa alfa")
        w.add_document(id=u"6", c=u"alfa")
        w.commit()
        
        s = ix.searcher(weighting=Frequency)
        q = query.Term("c", u"alfa")
        r = s.search(q)
        self.assertEqual([d["id"] for d in r], ["1", "2", "3", "4", "5", "6"])
        r = s.search_page(q, 2, pagelen=2)
        self.assertEqual([d["id"] for d in r], ["3", "4"])
        
        r = s.search_page(q, 10, pagelen=4)
        self.assertEqual(r.total, 6)
        self.assertEqual(r.pagenum, 2)
        self.assertEqual(r.pagelen, 2)
        


if __name__ == '__main__':
    unittest.main()
