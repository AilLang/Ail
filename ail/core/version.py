from os.path import exists, join
from .._config import CORE_PATH


AIL_MAIN_VERSION = 2
AIL_SUB_VERSION = [1,2]
AIL_VERSION_STATE = 'alpha'
AIL_VERSION = '%s.%s %s' % (AIL_MAIN_VERSION,
                            '.'.join([str(v) for v in AIL_SUB_VERSION]),
                            AIL_VERSION_STATE)


AIL_COPYRIGHT = '2019-2021 RDPStudio.'

AIL_INSTALL_TIME = -1


if exists(join(CORE_PATH, 'INSTALL_TIME')):
    try:
        with open(join(CORE_PATH, 'INSTALL_TIME')) as f:
            AIL_INSTALL_TIME = int(f.read())
    except:
        pass

