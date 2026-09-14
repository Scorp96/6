import asyncio,pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import gui_transport as gt
class R:
    def __init__(self,text='ok'): self.content=[type('P',(),{'type':'text','text':text})()]; self.isError=False
class C:
    def __init__(self): self.calls=[]; self.snaps=['Focused Window:\nGoogle Chrome','(100,200) 编辑 "与 ChatGPT 聊天" [focused]','(300,400) 按钮 "发送提示词"']
    async def call_tool(self,n,a):
        self.calls.append((n,a))
        if n=='Snapshot': return R(self.snaps.pop(0))
        if n=='Clipboard' and a.get('mode')=='get': return R('Clipboard content:\nhttps://chatgpt.com/')
        return R('ok')
class LongPromptTests(unittest.TestCase):
    def test_large_prompt_is_clipboard_pasted_in_bounded_chunks(self):
        c=C(); prompt='X'*6500
        asyncio.run(gt.open_new_chat_and_submit(c,prompt,page_wait_seconds=0))
        chunks=[a['text'] for n,a in c.calls if n=='Clipboard' and a.get('mode')=='set' and set(a.get('text',''))=={'X'}]
        self.assertGreater(len(chunks),1)
        self.assertEqual(''.join(chunks),prompt)
        self.assertLessEqual(max(map(len,chunks)),1000)
        self.assertEqual(sum(1 for n,a in c.calls if n=='Shortcut' and a.get('shortcut')=='ctrl+v'),len(chunks))
        type_texts=[str(a.get('text','')) for n,a in c.calls if n=='Type']
        self.assertNotIn(prompt,type_texts)
        self.assertFalse(any(text and set(text)=={'X'} for text in type_texts))
    def test_mcp_tool_allowlist_enables_type_for_control_only(self):
        src=pathlib.Path(gt.__file__).read_text(encoding='utf-8')
        self.assertIn('App,Shortcut,Clipboard,Click,Type,Wait,Snapshot',src)
if __name__=='__main__': unittest.main()
