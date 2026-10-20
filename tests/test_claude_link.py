import json,pathlib,tempfile,unittest,base64
from unittest.mock import patch
from shorekeeper_pet.claude_link import configure,own,status_command

class ClaudeLinkTests(unittest.TestCase):
    def test_preserve_foreign_hooks_status_and_uninstall_only_ours(self):
        original={'model':'test','hooks':{'Stop':[{'hooks':[{'type':'command','command':'other.exe'}]}]},
                  'statusLine':{'type':'command','command':'other-status.exe'}}
        with tempfile.TemporaryDirectory() as folder:
            home=pathlib.Path(folder);path=home/'settings.json';path.write_text(json.dumps(original),'utf8')
            with patch('shorekeeper_pet.claude_link.command',return_value=['C:/测试 & 项目/pet.exe']):
                report=configure(True,home,home)
                self.assertTrue(report['preserved_custom_statusline'])
                first=path.read_bytes();configure(True,home,home);self.assertEqual(first,path.read_bytes())
                configure(False,home,home);self.assertEqual(json.loads(path.read_text('utf8')),original)
            self.assertTrue(list((home/'shorekeeper-backups').glob('*.json')))

    def test_own_status_and_unicode_paths_survive_move(self):
        with tempfile.TemporaryDirectory() as folder:
            home=pathlib.Path(folder)
            for exe in ["C:/含 $ & ' 字符/pet.exe","D:/新的目录/pet.exe"]:
                with patch('shorekeeper_pet.claude_link.command',return_value=[exe]):
                    configure(True,home,home)
                data=json.loads((home/'settings.json').read_text('utf8'))
                self.assertTrue(own(data['statusLine']))
                self.assertEqual(data['hooks']['Stop'][0]['hooks'][0]['command'],exe)
                self.assertEqual(len(data['hooks']['Stop']),1)
            configure(False,home,home)
            self.assertEqual(json.loads((home/'settings.json').read_text('utf8')),{})

    def test_invalid_settings_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path=pathlib.Path(folder)/'settings.json';path.write_text('{broken','utf8')
            with self.assertRaises(ValueError):configure(True,folder,folder)
            self.assertEqual(path.read_text('utf8'),'{broken')

if __name__=='__main__':unittest.main()
