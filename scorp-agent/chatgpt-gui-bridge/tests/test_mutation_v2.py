import base64,json,pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import bridge_core as bc
import gui_transport as gt
RID='cont-v2-1'
M={'kind':'SET_NEXT_ACTION','next_action':{'protocol_version':'scorp.exec/v4','action_id':'a','action_kind':'powershell','payload':{'script':'throw "quoted"; $p="C:\\\\x"'}}}

def enc(obj):
    raw=json.dumps(obj,separators=(',',':'),ensure_ascii=False).encode('utf-8')
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')

class MutationV2Tests(unittest.TestCase):
    def test_extract_v2_survives_uia_text_escaping(self):
        snap='text \\"SCORP_GUI_MUTATION_V2::'+RID+'::'+enc(M)+'\\"\\n'
        self.assertEqual(bc.extract_mutation(snap,RID),M)

    def test_prompt_requires_v2_base64url(self):
        req={'request_id':RID,'task_id':'t','project_id':'p','question':'next','safety_class':'standard'}
        prompt=gt.render_controller_prompt(req,{'status':'SUCCEEDED'})
        self.assertIn('SCORP_GUI_MUTATION_V2',prompt)
        self.assertIn('base64url',prompt.lower())

if __name__=='__main__': unittest.main()
