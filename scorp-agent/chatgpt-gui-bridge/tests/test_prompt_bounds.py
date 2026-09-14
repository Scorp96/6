import pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import gui_transport as gt

REQ={'request_id':'cont-bound-1','task_id':'task','project_id':'proj','question':'Choose next action','safety_class':'standard'}

class PromptBoundsTests(unittest.TestCase):
    def test_large_v4_evidence_is_bounded_and_keeps_hashes(self):
        previous={'status':'SUCCEEDED','exit_code':0,'action_id':'a1','result_sha256':'r'*64,
                  'evidence':{'combined_tail':'C'*9000,'stdout_tail':'O'*5000,'stderr_tail':'E'*5000,
                              'stdout_sha256':'s'*64,'stderr_sha256':'e'*64,
                              'stdout_log_path':r'C:\logs\out.log','stderr_log_path':r'C:\logs\err.log'}}
        prompt=gt.render_controller_prompt(REQ,previous)
        self.assertLessEqual(len(prompt),7000)
        self.assertNotIn('C'*100,prompt)
        self.assertIn('s'*64,prompt)
        self.assertIn('e'*64,prompt)
        self.assertIn('C:\\\\logs\\\\out.log',prompt)

    def test_prompt_preserves_tail_end_not_head(self):
        previous={'status':'FAILED','evidence':{'stderr_tail':'HEAD-'+'x'*5000+'-ROOT_CAUSE_END'}}
        prompt=gt.render_controller_prompt(REQ,previous)
        self.assertIn('ROOT_CAUSE_END',prompt)
        self.assertNotIn('HEAD-',prompt)

if __name__=='__main__': unittest.main()
