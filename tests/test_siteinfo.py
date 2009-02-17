"""

uses py.test

sudo easy_install py

http://codespeak.net/py/dist/test.html

"""
import os
import sys
from datetime import datetime, timedelta
import unittest
from threading import Thread
from Queue import Queue
from Queue import Empty

from django.conf import settings

TEST_ROOT = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(TEST_ROOT + "/..")

sys.path = [ROOT] + sys.path

from hydeengine.file_system import File, Folder
from hydeengine import url, Initializer, Generator, setup_env
from hydeengine.siteinfo import SiteNode, SiteInfo, Page

TEST_ROOT = Folder(TEST_ROOT)
TEST_SITE = TEST_ROOT.child_folder("test_site")

def setup_module(module):
    Initializer(TEST_SITE.path).initialize(ROOT, force=True)
    setup_env(TEST_SITE.path)
    
def teardown_module(module):
    TEST_SITE.delete()
    
class TestSiteInfo:

    def setup_method(self, method):
        self.site = SiteInfo(settings, TEST_SITE.path)
        self.site.refresh()

    def assert_node_complete(self, node, folder):
        assert node.folder.path == folder.path
        test_case = self
        class Visitor(object):
            def visit_folder(self, folder):
                child = node.find_child(folder)
                assert child
                test_case.assert_node_complete(child, folder)
                
            def visit_file(self, a_file):
                assert node.find_resource(a_file)
                
        folder.list(Visitor())

    def test_population(self):
        assert self.site.name == "test_site"
        self.assert_node_complete(self.site, TEST_SITE)
        
    def test_type(self):
        def assert_node_type(node_dir, type):
           node = self.site.find_child(Folder(node_dir))
           assert node
           assert Folder(node_dir).same_as(node.folder)
           for child in node.walk():
               assert child.type == type
        assert_node_type(settings.CONTENT_DIR, "content")
        assert_node_type(settings.MEDIA_DIR, "media")
        assert_node_type(settings.LAYOUT_DIR, "layout")
        
    def test_attributes(self):
        for node in self.site.walk():
           self.assert_node_attributes(node)
           for resource in node.resources:
               self.assert_resource_attributes(resource)
                           
    def assert_node_attributes(self, node):
        fragment = self.get_node_fragment(node)
        if node.type == "content":
            fragment = node.folder.get_fragment(self.site.content_folder)
        elif node.type == "media":
            fragment = node.folder.get_fragment(self.site.folder)
        if node.type in ("content", "media"):
            fragment = ("/" + fragment.strip("/")).rstrip("/")
            assert fragment == node.url
            assert settings.SITE_WWW_URL + fragment == node.full_url
        else:    
            assert not node.url
            assert not node.full_url
                
        assert node.source_folder == node.folder
        if not node == self.site and node.type not in ("content", "media"):
            assert not node.target_folder
            assert not node.temp_folder
        else:
            assert node.target_folder.same_as(Folder(
                            os.path.join(settings.DEPLOY_DIR,
                                fragment.lstrip("/"))))
            assert node.temp_folder.same_as(Folder(
                            os.path.join(settings.TMP_DIR, 
                                fragment.lstrip("/"))))
                       
    def assert_resource_attributes(self, resource):
        node = resource.node
        fragment = self.get_node_fragment(node)
        if resource.node.type in ("content", "media"):
            assert (resource.url ==  
                        url.join(node.url, resource.file.name))
            assert (resource.full_url ==  
                        url.join(node.full_url, resource.file.name))
            assert resource.target_file.same_as(
                    File(node.target_folder.child(
                            resource.file.name)))
            assert resource.temp_file.same_as(
                    File(node.temp_folder.child(resource.file.name)))
        else:
            assert not resource.url
            assert not resource.full_url
        
        assert resource.source_file.parent.same_as(node.folder)
        assert resource.source_file.name == resource.file.name
        
    def get_node_fragment(self, node):
        fragment = ''
        if node.type == "content":
            fragment = node.folder.get_fragment(self.site.content_folder)
        elif node.type == "media":
            fragment = node.folder.get_fragment(self.site.folder)
        return fragment
        

class MonitorTests(object): 
    def clean_queue(self):
        while not self.queue.empty():
            try:
                self.queue.get()
                self.queue.task_done()
            except Empty:
                break
    
    def setup_class(cls):
        cls.site = None
        cls.queue = Queue()

    def teardown_class(cls):
        if cls.site:
            cls.site.dont_monitor()    
            
    def setup_method(self, method):
        self.site = SiteInfo(settings, TEST_SITE.path)
        self.site.refresh()
        self.exception_queue = Queue()
        self.clean_queue()
        
class TestSiteInfoMonitoring(MonitorTests):
    
    def change_checker(self, change, path):
        try:
            changes = self.queue.get(block=True, timeout=20)
            self.queue.task_done()
            assert changes
            assert not changes['exception']
            assert changes['change'] == change
            assert changes['resource']
            assert changes['resource'].file.path == path
        except:
            self.exception_queue.put(sys.exc_info())
            raise
            
    def test_monitor_stop(self):
        m = self.site.monitor()
        self.site.dont_monitor()
        assert not m.isAlive()
            
    def test_modify(self):
        self.site.monitor(self.queue)
        path = self.site.media_folder.child("css/base.css")
        t = Thread(target=self.change_checker, 
                    kwargs={"change":"Modified", "path":path})
        t.start()
        os.utime(path, None)
        t.join()
        assert self.exception_queue.empty()
        
    def test_add(self, direct=False):
        self.site.monitor(self.queue)
        path = self.site.layout_folder.child("test.ggg")
        t = Thread(target=self.change_checker, 
                    kwargs={"change":"Added", "path":path})
        t.start()      
        f = File(path)        
        f.write("test")
        t.join()
        if not direct:
            f.delete()
        assert self.exception_queue.empty()
        
    def test_delete(self):
        path = self.site.layout_folder.child("test.ggg")
        self.test_add(direct=True)
        t = Thread(target=self.change_checker, 
                    kwargs={"change":"Deleted", "path":path})
        t.start()      
        File(path).delete()
        t.join()
        assert self.exception_queue.empty()
        
class TestYAMLProcessor(MonitorTests):
   
    def yaml_checker(self, path, vars):
           try:
               changes = self.queue.get(block=True, timeout=5)
               self.queue.task_done()
               assert changes
               assert not changes['exception']
               resource = changes['resource']               
               assert resource
               assert resource.file.path == path
               # from hydeengine.content_processors import YAMLContentProcessor
               # YAMLContentProcessor.process(resource)
               for key, value in vars.iteritems():
                   assert hasattr(resource, key)
                   assert getattr(resource, key) == value
           except:
               self.exception_queue.put(sys.exc_info())
               raise    
    
    def test_variables_are_added(self):
        vars = {}
        vars["title"] = "Test Title"
        vars["created"] = datetime.now()
        vars["updated"] = datetime.now() + timedelta(hours=1)
        content = "{%hyde\n"
        for key, value in vars.iteritems():
            content += "    %s: %s\n" % (key, value)
        content +=  "%}"
        out = File(self.site.content_folder.child("test_yaml.html"))
        self.site.monitor(self.queue)
        t = Thread(target=self.yaml_checker, 
                        kwargs={"path":out.path, "vars":vars})
        t.start()
        out.write(content)
        t.join()
        assert self.exception_queue.empty()
        # Ensure default values are added for all pages
        #
        temp = File(self.site.content_folder.child("test.html"))
        temp.write('text')
        page = Page(temp, self.site)
        for key, value in vars.iteritems():
            assert hasattr(page, key)
            assert not getattr(page, key)
        temp.delete()
        out.delete()

class TestProcessing(MonitorTests):
    def checker(self, asserter):
           try:
               print "inside"
               changes = self.queue.get(block=True, timeout=5)
               self.queue.task_done()
               assert changes
               assert not changes['exception']
               resource = changes['resource']               
               assert resource
               asserter(resource)
           except:
               self.exception_queue.put(sys.exc_info())
               raise    

    def assert_valid_css(self, actual_css_resource):
        expected_text = File(
                TEST_ROOT.child("test_dest.css")).read_all()
        self.generator.process(actual_css_resource)

        # Ensure source file is not changed
        # The source should be copied to tmp and then
        # the processor should do its thing.
        original_source = File(
                TEST_ROOT.child("test_src.css")).read_all()
        source_text = actual_css_resource.file.read_all()
        assert original_source == source_text        
        actual_text = actual_css_resource.temp_file.read_all()        
        assert expected_text == actual_text
        
    def test_process_css_with_templates(self):
        original_MP = settings.MEDIA_PROCESSORS
        original_site = settings.SITE_ROOT
        settings.MEDIA_PROCESSORS = {"*":{".css":
        ('hydeengine.media_processors.TemplateProcessor',)}}
        settings.SITE_ROOT = "www.hyde-test.bogus/"
        self.generator = Generator(TEST_SITE.path)
        self.generator.build_siteinfo()
        source = File(TEST_ROOT.child("test_src.css"))
        self.site.refresh()
        self.site.monitor(self.queue)
        t = Thread(target=self.checker, 
                        kwargs={"asserter":self.assert_valid_css})
        t.start()
        source.copy_to(self.site.media_folder.child("test.css"))
        t.join()
        settings.MEDIA_PROCESSORS = original_MP
        settings.SITE_ROOT = original_site
        assert self.exception_queue.empty()