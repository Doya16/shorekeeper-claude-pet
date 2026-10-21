import os,pathlib,sys
ROOT=pathlib.Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else pathlib.Path(__file__).resolve().parents[1]
CLAUDE_HOME=pathlib.Path(os.environ.get('CLAUDE_CONFIG_DIR',pathlib.Path.home()/'.claude'))
VERSION='0.1.1'
APP_NAME='守岸人Claude桌宠启动'
EXECUTABLE_NAME=APP_NAME+'.exe'
PORTABLE_DIRNAME='守岸人Claude桌宠'
AUDIO_SESSION_NAME='守岸人 · Claude'
APPLICATION_ID='Doya16.Shorekeeper.Claude'

UPDATE_REPOSITORY='Doya16/shorekeeper-claude-pet'
