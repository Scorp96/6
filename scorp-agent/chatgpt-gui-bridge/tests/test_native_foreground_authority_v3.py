import asyncio, pathlib, sys, unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import gui_transport as gt
class Part:
 type='text'
 def __init__(self,text): self.text=text
class Result:
 isError=False
 def __init__(self,text='ok'): self.content=[Part(text)]
class Client:
 def __init__(self,snapshots): self.snapshots=list(snapshots); self.calls=[]
 async def call_tool(self,name,args):
  self.calls.append((name,args))
  if name=='Snapshot': return Result(self.snapshots.pop(0))
  if name=='Clipboard':
   if args.get('mode')=='get': return Result('Clipboard content:\nhttps://chatgpt.com/')
   return Result()
  return Result()
class NativeForegroundAuthorityTests(unittest.TestCase):
 def test_native_chrome_allows_missing_snapshot_focus_section(self):
  c=Client(['Cursor Position: (1,1)\nActive Desktop:\nDesktop 1'])
  out=asyncio.run(gt.acquire_chatgpt_window(c,wait_seconds=0,foreground_timeout_seconds=1,foreground_poll_seconds=0,foreground_max_attempts=1,foreground_window_is_chrome_fn=lambda: True))
  self.assertIn('Active Desktop',out)
 def test_native_non_chrome_still_fails_closed(self):
  c=Client(['Focused Window:\nChatGPT - Google Chrome\nOpened Windows:'])
  with self.assertRaisesRegex(RuntimeError,'CHROME_FOREGROUND_NOT_ACQUIRED'):
   asyncio.run(gt.acquire_chatgpt_window(c,wait_seconds=0,foreground_timeout_seconds=1,foreground_poll_seconds=0,foreground_max_attempts=1,foreground_window_is_chrome_fn=lambda: False))
if __name__=='__main__': unittest.main()