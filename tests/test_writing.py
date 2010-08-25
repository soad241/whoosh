import unittest

import os, random

from whoosh import fields, index, writing


class TestWriting(unittest.TestCase):
    def make_dir(self, name):
        if not os.path.exists(name):
            os.mkdir(name)
    
    def destroy_dir(self, name):
        try:
            os.rmdir("testindex")
        except:
            pass
    
    def clean_file(self, path):
        if os.path.exists(path):
            os.remove(path)
    
    def test_asyncwriter(self):
        self.make_dir("testindex")
        schema = fields.Schema(id=fields.ID, text=fields.TEXT)
        ix = index.create_in("testindex", schema)
        
        domain = (u"alfa", u"bravo", u"charlie", u"delta", u"echo", u"foxtrot", u"golf", u"hotel", u"india")
        
        writers = []
        for i in xrange(20):
            w = writing.AsyncWriter(ix.writer)
            # Simulate doing 20 (near-)simultaneous commits. If we weren't using
            # AsyncWriter, at least some of these would fail because the first
            # writer wouldn't be finished yet.
            writers.append(w)
            w.add_document(id=unicode(i), text=u" ".join(random.sample(domain, 5)))
            w.commit()
        
        # Wait for all writers to finish before checking the results
        for w in writers:
            if w.running:
                w.join()
        
        # Check whether all documents made it into the index.
        r = ix.reader()
        self.assertEqual(sorted([int(id) for id in r.lexicon("id")]), range(20))



if __name__ == '__main__':
    unittest.main()
