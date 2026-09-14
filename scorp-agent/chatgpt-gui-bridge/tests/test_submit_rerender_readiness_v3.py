import asyncio,pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import gui_transport as gt
class Part:
 type='text'
 def __init__(self,text): self.text=text
class Result:
 isError=False
 def __init__(self,text='ok'): self.content=[Part(text)]
class Client:
 def __init__(self):
  self.snapshots=[
   'Cursor Position: (1,1)\nActive Desktop:',
   '(400,700) edit \"Message ChatGPT\"',
   'ChatGPT is updating the composer',
   '(900,700) button \"Send message\"',
  ]
  self.calls=[]
  self.clipboard_gets=[
   'Clipboard content:\noriginal',
   'Clipboard content:\nhttps://chatgpt.com/',
   'Clipboard content:\noriginal',
  ]
 async def call_tool(self,name,args):
  self.calls.append((name,args))
  if name=='Snapshot': return Result(self.snapshots.pop(0))
  if name=='Clipboard':
   if args.get('mode')=='get': return Result(self.clipboard_gets.pop(0))
   return Result()
  return Result()
class SubmitRerenderReadinessTests(unittest.TestCase):
 def test_transient_post_paste_snapshot_retries_until_send_control_appears(self):
  c=Client()
  asyncio.run(gt.open_new_chat_and_submit(c,'PROMPT',page_wait_seconds=0,foreground_window_is_chrome_fn=lambda:True,submit_timeout_seconds=1,submit_poll_seconds=0,submit_max_attempts=2))
  clicks=[args for name,args in c.calls if name=='Click']
  self.assertEqual([{'loc':[900,700]}],clicks)
  self.assertEqual(4,sum(1 for name,_ in c.calls if name=='Snapshot'))
if __name__=='__main__': unittest.main()
