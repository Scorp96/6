import json
import pathlib
import unittest

from bridge_worker import GitHubCli


class FakeCompleted:
    def __init__(self, code=0, stdout='{"id":9}', stderr='warning'):
        self.returncode = code
        self.stdout = stdout
        self.stderr = stderr


class GitHubCommentRegressionTests(unittest.TestCase):
    def test_post_comment_uses_json_file_not_literal_at_dash(self):
        seen = {}
        def runner(argv, **kwargs):
            seen['argv'] = list(argv)
            input_path = pathlib.Path(argv[argv.index('--input') + 1])
            seen['payload'] = json.loads(input_path.read_text(encoding='utf-8'))
            return FakeCompleted()
        gh = GitHubCli('Scorp96/scorp-control-plane', runner=runner)
        self.assertEqual(gh.post_comment(7, 'hello')['id'], 9)
        self.assertIn('--input', seen['argv'])
        self.assertNotIn('body=@-', seen['argv'])
        self.assertNotEqual(seen['argv'][seen['argv'].index('--input') + 1], '-')
        self.assertEqual(seen['payload'], {'body': 'hello'})


if __name__ == '__main__':
    unittest.main()
