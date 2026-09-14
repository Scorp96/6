import asyncio
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
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

class ChromeAcquisitionTests(unittest.TestCase):
    def acquire(self, client, **kwargs):
        self.assertTrue(hasattr(gt,'acquire_chatgpt_window'),'missing acquire_chatgpt_window')
        return asyncio.run(gt.acquire_chatgpt_window(client, wait_seconds=0, **kwargs))

    def test_launches_dedicated_chatgpt_window(self):
        c=Client(['Focused Window:\nChatGPT - Google Chrome\nOpened Windows:'])
        self.acquire(c)
        self.assertEqual(c.calls[0][0],'App')
        self.assertEqual(c.calls[0][1]['mode'],'launch_executable')
        self.assertIn('--new-window',c.calls[0][1]['args'])
        self.assertIn('https://chatgpt.com/',c.calls[0][1]['args'])

    def test_transient_wrong_focus_waits_for_launched_chrome(self):
        c=Client([
            'Focused Window:\nAdministrator: cmd.exe\nOpened Windows:\nChatGPT - Google Chrome',
            'Focused Window:\nChatGPT - Google Chrome\nOpened Windows:',
        ])
        snapshot=self.acquire(
            c,
            foreground_timeout_seconds=1,
            foreground_poll_seconds=0,
            foreground_max_attempts=2,
        )
        self.assertIn('Google Chrome', snapshot)
        self.assertEqual(2, sum(1 for name,_ in c.calls if name=='Snapshot'))

    def test_wrong_focus_fails_closed_after_bounded_attempts(self):
        c=Client([
            'Focused Window:\nAdministrator: cmd.exe\nOpened Windows:\nChatGPT - Google Chrome',
            'Focused Window:\nAdministrator: cmd.exe\nOpened Windows:\nChatGPT - Google Chrome',
        ])
        with self.assertRaisesRegex(RuntimeError,'CHROME_FOREGROUND_NOT_ACQUIRED'):
            self.acquire(
                c,
                foreground_timeout_seconds=1,
                foreground_poll_seconds=0,
                foreground_max_attempts=2,
            )
        shortcuts=[x for x in c.calls if x[0]=='Shortcut']
        self.assertEqual(shortcuts,[])

    def test_focus_parser_ignores_chrome_only_in_opened_windows(self):
        self.assertTrue(hasattr(gt,'focused_window_is_chrome'),'missing focused_window_is_chrome')
        snap=('Focused Window:\nAdministrator: cmd.exe\n'
              'Opened Windows:\nChatGPT - Google Chrome')
        self.assertFalse(gt.focused_window_is_chrome(snap))

if __name__=='__main__':
    unittest.main()
class ChromeClosureTests(unittest.TestCase):
    def test_completed_owned_window_closes(self):
        self.assertTrue(hasattr(gt,'close_completed_chat_window'),'missing close helper')
        rid='cont-close-1'
        snap=('Focused Window:\nChatGPT - Google Chrome\nOpened Windows:\n'
              f'text "SCORP_GUI_MUTATION_V1::{rid}::{{}}"')
        c=Client([])
        closed=asyncio.run(gt.close_completed_chat_window(c,snap,rid))
        self.assertTrue(closed)
        self.assertIn(('Shortcut',{'shortcut':'alt+f4'}),c.calls)

    def test_unowned_or_unfocused_window_not_closed(self):
        self.assertTrue(hasattr(gt,'close_completed_chat_window'),'missing close helper')
        c=Client([])
        snap='Focused Window:\nAdministrator: cmd.exe\nOpened Windows:\nChatGPT - Google Chrome'
        closed=asyncio.run(gt.close_completed_chat_window(c,snap,'cont-close-2'))
        self.assertFalse(closed)
        self.assertEqual(c.calls,[])